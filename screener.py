#!/usr/bin/env python3
"""
Daily stock screener — read-only, data and flags only.
No order execution. No buy/sell recommendations.
Every output row carries auditor: VERIFY.
"""

import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # suppress internal 404 noise


# ── Config & universe ─────────────────────────────────────────────────────────

def load_config() -> dict:
    with open("config.json") as f:
        return json.load(f)


def load_tickers() -> list[str]:
    with open("tickers.txt") as f:
        return [
            line.strip().upper()
            for line in f
            if line.strip() and not line.startswith("#")
        ]


# ── Technical indicators ──────────────────────────────────────────────────────

def compute_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    prev_cl = cl.shift(1)
    tr = pd.concat(
        [hi - lo, (hi - prev_cl).abs(), (lo - prev_cl).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean()


def find_swing_lows(df: pd.DataFrame, n: int) -> list[float]:
    """Bars where the low is the minimum of the surrounding 2n+1 window."""
    lows = df["Low"].values
    pivots = []
    for i in range(n, len(lows) - n):
        window = lows[i - n : i + n + 1]
        if lows[i] == window.min() and list(window).count(lows[i]) == 1:
            pivots.append(float(lows[i]))
    return pivots


def find_swing_highs(df: pd.DataFrame, n: int) -> list[float]:
    """Bars where the high is the maximum of the surrounding 2n+1 window."""
    highs = df["High"].values
    pivots = []
    for i in range(n, len(highs) - n):
        window = highs[i - n : i + n + 1]
        if highs[i] == window.max() and list(window).count(highs[i]) == 1:
            pivots.append(float(highs[i]))
    return pivots


def cluster_zones(prices: list[float], cluster_pct: float) -> list[float]:
    """Group nearby prices into zone midpoints."""
    if not prices:
        return []
    sorted_prices = sorted(prices)
    zones, cluster = [], [sorted_prices[0]]
    for p in sorted_prices[1:]:
        if (p - cluster[0]) / cluster[0] <= cluster_pct:
            cluster.append(p)
        else:
            zones.append(round(sum(cluster) / len(cluster), 2))
            cluster = [p]
    zones.append(round(sum(cluster) / len(cluster), 2))
    return zones


def higher_highs_higher_lows(df: pd.DataFrame, lookback: int, n: int) -> bool:
    """True if the last two swing highs and last two swing lows are both ascending."""
    recent = df.iloc[-lookback:]
    highs = find_swing_highs(recent, n=n)
    lows = find_swing_lows(recent, n=n)
    hh = len(highs) >= 2 and highs[-1] > highs[-2]
    hl = len(lows) >= 2 and lows[-1] > lows[-2]
    return hh and hl


def relative_strength_3m(
    df: pd.DataFrame, df_spy: pd.DataFrame | None, lookback: int
) -> float | None:
    """Ticker 3-month return divided by SPY 3-month return. >1 = outperforming."""
    if df_spy is None or len(df) < lookback or len(df_spy) < lookback:
        return None
    spy_ret = float(df_spy["Close"].iloc[-1] / df_spy["Close"].iloc[-lookback] - 1)
    if spy_ret == 0:
        return None
    ticker_ret = float(df["Close"].iloc[-1] / df["Close"].iloc[-lookback] - 1)
    return round(ticker_ret / spy_ret, 4)


# ── Calendar flags ────────────────────────────────────────────────────────────

def get_calendar_flags(ticker: yf.Ticker, lookahead: int) -> dict:
    today = pd.Timestamp.today().normalize()
    result = {"earnings_date": None, "exdiv_date": None}

    try:
        cal = ticker.calendar
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
        else:
            dates = []
        for raw in dates:
            ts = pd.Timestamp(raw).normalize()
            bdays = len(pd.bdate_range(today, ts))
            if 0 < bdays <= lookahead:
                result["earnings_date"] = str(ts.date())
                break
    except Exception:
        pass

    try:
        exdiv_raw = ticker.info.get("exDividendDate")
        if exdiv_raw:
            ts = pd.Timestamp(exdiv_raw, unit="s").normalize()
            bdays = len(pd.bdate_range(today, ts))
            if 0 < bdays <= lookahead:
                result["exdiv_date"] = str(ts.date())
    except Exception:
        pass

    return result


# ── Reasons builder ──────────────────────────────────────────────────────────

def build_reasons(
    price: float, sma_now: float, sma_ago: float, hh_hl: bool,
    atr_pct: float, rs: float | None, cal: dict, cfg: dict,
) -> list[str]:
    def ok(cond: bool) -> str:
        return "[Y]" if cond else "[N]"

    reasons = []
    above = price > sma_now
    reasons.append(f"Price ${price:.2f} {'above' if above else 'below'} 50-SMA ${sma_now:.2f} {ok(above)}")

    rising = sma_now > sma_ago
    reasons.append(f"50-SMA {'rising' if rising else 'flat/falling'} (was ${sma_ago:.2f} ten bars ago) {ok(rising)}")

    reasons.append(f"Trend structure HH/HL: {ok(hh_hl)}")

    lo, hi = cfg["atr_min_pct"], cfg["atr_max_pct"]
    in_band = lo <= atr_pct <= hi
    reasons.append(f"ATR {atr_pct:.1f}% {'within' if in_band else 'outside'} {lo}-{hi}% volatility band {ok(in_band)}")

    if rs is not None:
        label = "outperforming SPY [Y]" if rs >= 1 else "underperforming SPY [N]"
        reasons.append(f"3m RS vs SPY {rs:.2f} - {label}")
    else:
        reasons.append("3m RS vs SPY: unavailable")

    if cal["earnings_date"]:
        reasons.append(f"[!] Earnings approaching: {cal['earnings_date']}")
    if cal["exdiv_date"]:
        reasons.append(f"[!] Ex-dividend: {cal['exdiv_date']}")

    max_dev = cfg.get("max_pct_above_sma", 30)
    pct_above_sma = (price - sma_now) / sma_now * 100 if sma_now else 0
    if pct_above_sma > max_dev:
        reasons.append(
            f"[!] Overextended: price {pct_above_sma:.0f}% above 50-SMA"
            f" — watch for pullback toward ${sma_now:.2f}"
        )

    return reasons


# ── Per-ticker screening ──────────────────────────────────────────────────────

def screen_one(symbol: str, cfg: dict, df_spy: pd.DataFrame | None) -> dict | None:
    min_bars = cfg["sma_period"] + cfg["sma_rising_lookback"] + 5

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1y")
    except Exception as e:
        log.warning(f"{symbol}: fetch failed — {e}")
        return None

    if df.empty or len(df) < min_bars:
        log.warning(f"{symbol}: only {len(df)} bars, need {min_bars} — skipping")
        return None

    price = float(df["Close"].iloc[-1])
    if price >= cfg["max_price"]:
        log.info(f"{symbol}: price ${price:.2f} >= ${cfg['max_price']} — filtered out")
        return None

    # ── SMA ──
    sma = compute_sma(df["Close"], cfg["sma_period"])
    sma_now = float(sma.iloc[-1])
    sma_ago = float(sma.iloc[-1 - cfg["sma_rising_lookback"]])

    # ── ATR ──
    atr_series = compute_atr(df, cfg["atr_period"])
    atr_val = float(atr_series.iloc[-1])
    atr_pct = round(atr_val / price * 100, 2)

    # ── 52-week range ──
    high_52w = float(df["High"].tail(252).max())
    low_52w = float(df["Low"].tail(252).min())
    pct_below_high = round((high_52w - price) / high_52w * 100, 2)
    pct_above_low = round((price - low_52w) / low_52w * 100, 2)

    # ── Trend structure ──
    hh_hl = higher_highs_higher_lows(
        df, lookback=cfg["trend_lookback"], n=cfg["pivot_n"]
    )

    # ── Support / resistance zones (full year, labelled for chart confirmation) ──
    n = cfg["pivot_n"]
    cpct = cfg["cluster_pct"]
    all_support = cluster_zones(find_swing_lows(df, n), cpct)
    all_resistance = cluster_zones(find_swing_highs(df, n), cpct)
    support_zones = [
        f"${z} — confirm on chart"
        for z in sorted([z for z in all_support if z < price], reverse=True)[:3]
    ]
    resistance_zones = [
        f"${z} — confirm on chart"
        for z in sorted([z for z in all_resistance if z > price])[:3]
    ]

    # ── Relative strength vs SPY ──
    rs = relative_strength_3m(df, df_spy, cfg["rs_lookback"])

    # ── Ranking (sort only, not a recommendation) ──
    if rs is not None:
        rank_value = rs
        rank_field = "rs_3m_vs_spy"
    else:
        rank_value = -pct_below_high  # closer to high ranks higher
        rank_field = "pct_below_52w_high_fallback"

    # ── Calendar ──
    cal = get_calendar_flags(ticker, cfg["calendar_lookahead"])

    pct_above_sma50 = round((price - sma_now) / sma_now * 100, 1) if sma_now else 0.0
    max_dev = cfg.get("max_pct_above_sma", 30)
    overextended = pct_above_sma50 > max_dev

    reasons = build_reasons(price, sma_now, sma_ago, hh_hl, atr_pct, rs, cal, cfg)

    return {
        "symbol": symbol,
        "price": round(price, 2),
        "sma_50": round(sma_now, 2),
        "pct_above_sma50": pct_above_sma50,
        "overextended": overextended,
        "price_above_sma50": bool(price > sma_now),
        "sma50_rising": bool(sma_now > sma_ago),
        "higher_highs_higher_lows": bool(hh_hl),
        "atr_14": round(atr_val, 2),
        "atr_pct_of_price": atr_pct,
        "atr_in_band": bool(cfg["atr_min_pct"] <= atr_pct <= cfg["atr_max_pct"]),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "pct_below_52w_high": pct_below_high,
        "pct_above_52w_low": pct_above_low,
        "rs_3m_vs_spy": rs,
        "rank_value": rank_value,
        "rank_field": rank_field,
        "support_zones": support_zones,
        "resistance_zones": resistance_zones,
        "earnings_date": cal["earnings_date"],
        "exdiv_date": cal["exdiv_date"],
        "earnings_flag": cal["earnings_date"] is not None,
        "exdiv_flag": cal["exdiv_date"] is not None,
        "reasons": reasons,
        "auditor": "VERIFY",
        "run_date": str(date.today()),
    }


# ── Telegram ──────────────────────────────────────────────────────────────────

def telegram_post(token: str, chat_id: str, text: str) -> None:
    """Low-level Telegram send. Raises on failure."""
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    resp.raise_for_status()


def send_telegram(
    token: str | None,
    chat_id: str | None,
    candidates: list[dict],
    watchlist: list[dict],
    top_n: int,
) -> None:
    if not token or not chat_id:
        log.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_USER_ID not set — skipping notification")
        return

    lines = [f"📊 Screener {date.today()} — top {min(top_n, len(candidates))} (data only, not a recommendation)\n"]
    for row in candidates[:top_n]:
        rs_str = f"RS {row['rs_3m_vs_spy']:.2f}" if row.get("rs_3m_vs_spy") is not None else ""
        header = " · ".join(filter(None, [f"*{row['symbol']}* ${row['price']}", rs_str]))
        lines.append(header)

        # Up to 2 key reasons: warnings first, then first passing rule
        key = [r for r in row.get("reasons", []) if r.startswith("[!]")]
        key += [r for r in row.get("reasons", []) if "[Y]" in r][:2]
        for r in key[:3]:
            lines.append(f"  {r}")

    if watchlist:
        syms = ", ".join(r["symbol"] for r in watchlist[:10])
        lines.append(f"\n👀 *Watchlist* ({len(watchlist)} overextended >30% above SMA): {syms}")

    lines.append("\n_auditor: VERIFY — human must confirm on chart_")
    msg = "\n".join(lines)

    try:
        telegram_post(token, chat_id, msg)
        log.info("Telegram message sent")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = load_config()
    tickers = load_tickers()
    log.info(f"Universe: {len(tickers)} tickers")

    # Pre-fetch SPY for relative-strength baseline
    df_spy: pd.DataFrame | None = None
    try:
        df_spy = yf.Ticker("SPY").history(period="1y")
        if df_spy.empty:
            df_spy = None
            log.warning("SPY data empty — RS will fall back to pct_below_52w_high")
    except Exception as e:
        log.warning(f"SPY fetch failed ({e}) — RS will fall back to pct_below_52w_high")

    results, filtered, skipped = [], [], []

    for symbol in tickers:
        log.info(f"Processing {symbol}")
        row = screen_one(symbol, cfg, df_spy)
        if row is None:
            # Distinguish price-filtered from data-skipped via log messages above;
            # here we just track total non-results.
            skipped.append(symbol)
        else:
            results.append(row)
        time.sleep(0.4)  # gentle rate-limiting

    candidates = [r for r in results if not r["overextended"]]
    watchlist  = [r for r in results if r["overextended"]]
    log.info(
        f"Candidates: {len(candidates)} | Watchlist (overextended): {len(watchlist)}"
        f" | Skipped/filtered: {len(skipped)}"
    )

    # Sort candidates by rank_value descending — sort only, not a recommendation
    candidates.sort(key=lambda r: r["rank_value"], reverse=True)
    for i, row in enumerate(candidates, start=1):
        row["rank"] = i

    # Watchlist sorted by RS descending too — for reference
    watchlist.sort(key=lambda r: r["rank_value"], reverse=True)
    for i, row in enumerate(watchlist, start=1):
        row["watchlist_rank"] = i

    # Write JSON output
    Path("results").mkdir(exist_ok=True)
    output = {
        "run_date": str(date.today()),
        "run_time_utc": datetime.now(timezone.utc).isoformat(),
        "config": cfg,
        "tickers_requested": tickers,
        "tickers_skipped": skipped,
        "candidate_count": len(candidates),
        "watchlist_count": len(watchlist),
        "candidates": candidates,
        "watchlist": watchlist,
    }
    out_path = Path("results/latest.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    log.info(f"Results written → {out_path}")

    # Telegram summary
    send_telegram(
        os.environ.get("TELEGRAM_BOT_TOKEN"),
        os.environ.get("TELEGRAM_USER_ID"),
        candidates,
        watchlist,
        cfg["telegram_top_n"],
    )


if __name__ == "__main__":
    main()
