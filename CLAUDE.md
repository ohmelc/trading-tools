# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Rules

1. **Read-only tools only.** This project builds read-only trading-analysis tools. No feature, script, or helper may submit, modify, or cancel orders against any broker or trading API — data retrieval and calculation only.

2. **No order-execution code.** Never write code that calls order-entry, order-amendment, or order-cancellation endpoints. Any function that could mutate broker or exchange state is out of scope.

3. **Auditor column required.** Every stock-screener output must include an `auditor: VERIFY` column. This is a compliance requirement for independence-restriction rules and must never be omitted or renamed.

4. **Position-sizing hard cap.** All position-sizing logic must cap risk at 1.5% of account value per trade. Any sizing calculation that would breach that limit must refuse to produce a result and raise an explicit error instead.

## Architecture

```
screener.py          ← daily screener (Python, yfinance, no broker calls)
tickers.txt          ← universe — one ticker per line, # to comment
config.json          ← all tuneable parameters
requirements.txt     ← pip dependencies
results/latest.json  ← screener output (gitignored; uploaded as Actions artifact)
calculator.html      ← standalone position-size calculator (HTML/CSS/JS)
.github/workflows/screener.yml  ← GitHub Actions cron (21:30 UTC Mon–Fri)
```

## Dev commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run screener locally (reads .env for Telegram credentials)
python screener.py

# Telegram credentials go in .env (gitignored):
#   TELEGRAM_TOKEN=...
#   TELEGRAM_CHAT_ID=...
```

## Screener output contract

Every `candidates` entry in `results/latest.json` always carries:
- `auditor: "VERIFY"` — never omit, never auto-clear
- `support_zones` / `resistance_zones` — labelled "confirm on chart"
- No overall verdict field — the tool never outputs a go/no-go signal

## GitHub Actions

Secrets required in the repo (Settings → Secrets → Actions):
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

To trigger manually: Actions tab → "Daily Stock Screener" → Run workflow.
