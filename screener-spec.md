# Stock Screener — Build Specification

## Purpose
A read-only daily stock screener. It pulls historical price data, computes a set
of trend/volatility rules, suggests support/resistance levels, flags upcoming
calendar events, and outputs a ranked candidate list. It does NOT place trades,
does NOT recommend buy/sell, and does NOT decide entries or stops. It surfaces
data and flags so a human can make the decision from the chart.

## Hard guardrails (also enforced by CLAUDE.md)
- Read-only. The tool may pull market data, compute, write a JSON file, and send
  a Telegram message. It must NEVER call any broker or trading API, and never
  submit, modify, or cancel an order.
- Support/resistance levels are SUGGESTIONS only, labelled "confirm on chart".
  The tool never places or recommends a stop.
- The auditor field is always output as "VERIFY". It is never auto-cleared.
  (This is a personal independence-compliance requirement.)
- No overall verdict. The tool never outputs "OK to enter", "buy", "sell", or any
  single go/no-go conclusion. Output is data and flags only.

## Data source
- Use `yfinance` (free, no API key) for daily OHLC history. Pull ~1 year of daily
  bars per ticker.
- Use yfinance for upcoming earnings dates and ex-dividend dates.
- Wrap every per-ticker fetch in try/except: if one ticker fails or rate-limits,
  skip it, log which tickers were skipped, and continue the run. A single failure
  must not crash the whole job.

## Universe
- Start SMALL: a hardcoded list of ~30-50 tickers (to be provided by the user).
  Do not screen the whole S&P 500 on day one.
- Filter to last close < $100 (sub-$100 names only).

## Rules computed per ticker

### Auto-PASS rules (pure arithmetic — trustworthy)
- Price above 50-day SMA. Store and display both the price and the 50-day SMA value.
- 50-day SMA rising: today's 50-day SMA > 50-day SMA from 10 bars ago.
- Trend structure: higher-highs and higher-lows over the last ~40 bars.
- ATR(14) value.
- Distance of current price from the 52-week high (as a %).
- Volatility band: ATR as a % of price falls within a configurable range
  (default 2%-4%).

### Auto-SUGGEST (computed, but shown as candidates to confirm — NOT pass/fail)
- Candidate SUPPORT levels: detect swing-low pivots (a low with N lower-lows on
  each side; start N=5, make N a parameter), cluster nearby pivots into zones.
  Output the zones labelled "possible support — confirm on chart".
- Candidate RESISTANCE levels: same logic on swing highs.

### Auto-FLAG
- Earnings within the next 10 trading days (flag with the date).
- Ex-dividend within the next 10 trading days (flag with the date).
- Auditor: always output the literal value "VERIFY".

## Output
- A ranked candidate list. Ranking metric: relative strength / distance-from-low
  (make the ranking field configurable; default to % above 52-week low).
- Write the full result as a JSON file committed to the repo. Each row must carry
  ALL computed fields (price, 50MA, ATR, 52wk position, trend flags, suggested
  S/R zones, calendar flags, auditor:VERIFY) so a later checklist UI can auto-fill
  from it.
- Also send a Telegram message with the top N candidates (N configurable,
  default 5), one line each: ticker, price, key flags, and any earnings/dividend
  warning.

## Delivery / hosting (keep it free)
- Run the script on a daily schedule via GitHub Actions cron (no paid server).
- The job: run screener -> write JSON to repo -> send Telegram push.
- Telegram bot token and chat ID via environment variables / GitHub secrets,
  never hardcoded.

## Build process request
Before writing any code: summarise back what you intend to build, list the
configurable parameters you'll expose, and ask any clarifying questions
(especially: the ticker universe, the Telegram credentials approach, and the
ranking metric). Only start coding after I confirm.
