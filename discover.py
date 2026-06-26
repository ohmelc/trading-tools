#!/usr/bin/env python3
"""
Universe discovery — scans S&P 500 + Russell 2000 for new tickers that pass
the screener criteria and appends them to tickers.txt.
Read-only data analysis only. No order execution. auditor: VERIFY on all output.
"""

import logging
import os
import time
from datetime import date
from io import StringIO

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

from screener import load_config, screen_one, telegram_post

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# ── Universe fetchers ─────────────────────────────────────────────────────────

def fetch_sp500() -> list[str]:
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            attrs={"id": "constituents"},
        )
        symbols = tables[0]["Symbol"].str.replace(".", "-", regex=False).str.strip().tolist()
        log.info(f"S&P 500: {len(symbols)} symbols fetched")
        return symbols
    except Exception as e:
        log.error(f"S&P 500 fetch failed: {e}")
        return []


def fetch_russell2000() -> list[str]:
    """Download IWM (iShares Russell 2000 ETF) holdings CSV."""
    url = (
        "https://www.ishares.com/us/products/239726/"
        "ishares-russell-2000-etf/1467271812596.ajax"
        "?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.ishares.com"},
            timeout=30,
        )
        resp.raise_for_status()
        lines = resp.text.splitlines()
        # Holdings CSV has a preamble; find the row starting with "Ticker"
        header_idx = next((i for i, l in enumerate(lines) if l.startswith("Ticker")), None)
        if header_idx is None:
            log.warning("Russell 2000: could not find Ticker header in CSV")
            return []
        df = pd.read_csv(StringIO("\n".join(lines[header_idx:])))
        tickers = (
            df["Ticker"].dropna().astype(str).str.strip()
            .pipe(lambda s: s[s.str.match(r"^[A-Z]{1,5}$")])
        )
        log.info(f"Russell 2000: {len(tickers)} symbols fetched")
        return tickers.tolist()
    except Exception as e:
        log.warning(f"Russell 2000 fetch failed: {e}")
        return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_current_universe() -> set[str]:
    with open("tickers.txt") as f:
        return {
            line.strip().upper()
            for line in f
            if line.strip() and not line.startswith("#")
        }


def batch_last_prices(symbols: list[str], batch_size: int = 100) -> dict[str, float]:
    """Return {symbol: last_close} for each symbol via batched yf.download."""
    prices: dict[str, float] = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i : i + batch_size]
        try:
            data = yf.download(batch, period="5d", auto_adjust=True, progress=False)
            if data.empty:
                continue
            close = data["Close"] if "Close" in data.columns else data.xs("Close", axis=1, level=0)
            # Normalise to DataFrame with symbols as columns
            if isinstance(close, pd.Series):
                close = close.to_frame(name=batch[0])
            for sym in batch:
                if sym in close.columns:
                    last = close[sym].dropna()
                    if not last.empty:
                        prices[sym] = float(last.iloc[-1])
        except Exception as e:
            log.warning(f"Batch price fetch {i//batch_size + 1} failed: {e}")
        time.sleep(0.5)
    return prices


def append_tickers(new_symbols: list[str]) -> None:
    with open("tickers.txt", "a") as f:
        f.write(f"\n# Auto-discovered {date.today()}\n")
        for sym in new_symbols:
            f.write(f"{sym}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = load_config()
    current = load_current_universe()
    log.info(f"Current universe: {len(current)} tickers")

    sp500 = fetch_sp500()
    r2000 = fetch_russell2000()

    candidates = sorted({*sp500, *r2000} - current)
    log.info(f"New candidates to evaluate: {len(candidates)}")

    if not candidates:
        log.info("Nothing new to evaluate.")
        return

    # Pre-fetch SPY for RS calculation
    df_spy: pd.DataFrame | None = None
    try:
        df_spy = yf.Ticker("SPY").history(period="1y")
        if df_spy.empty:
            df_spy = None
    except Exception:
        pass

    # Step 1: batch price pre-filter
    log.info("Batch price pre-filter...")
    price_map = batch_last_prices(candidates)
    price_qualified = [
        s for s in candidates
        if price_map.get(s, 999) < cfg["max_price"]
    ]
    log.info(f"{len(price_qualified)} pass price < ${cfg['max_price']} filter")

    # Step 2: full screener on price-qualified tickers
    new_additions: list[str] = []
    for sym in price_qualified:
        log.info(f"Screening {sym}")
        row = screen_one(sym, cfg, df_spy)
        if row is not None:
            new_additions.append(sym)
            log.info(f"  ✓ {sym} qualifies — will add to universe")
        time.sleep(0.4)

    # Step 3: append to tickers.txt
    if new_additions:
        append_tickers(new_additions)
        log.info(f"Added {len(new_additions)} ticker(s): {new_additions}")
    else:
        log.info("No new qualifying tickers found.")

    # Step 4: Telegram notification
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_USER_ID")
    if token and chat_id:
        if new_additions:
            body = ", ".join(new_additions)
            msg = (
                f"🔍 *Discovery run {date.today()}*\n"
                f"Added {len(new_additions)} new ticker(s) to universe:\n"
                f"{body}\n\n"
                f"_auditor: VERIFY — confirm suitability before acting_"
            )
        else:
            msg = (
                f"🔍 *Discovery run {date.today()}*\n"
                f"No new qualifying tickers found in S&P 500 + Russell 2000."
            )
        try:
            telegram_post(token, chat_id, msg)
            log.info("Telegram notification sent")
        except Exception as e:
            log.error(f"Telegram send failed: {e}")


if __name__ == "__main__":
    main()
