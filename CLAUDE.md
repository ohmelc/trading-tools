# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Rules

1. **Read-only tools only.** This project builds read-only trading-analysis tools. No feature, script, or helper may submit, modify, or cancel orders against any broker or trading API — data retrieval and calculation only.

2. **No order-execution code.** Never write code that calls order-entry, order-amendment, or order-cancellation endpoints. Any function that could mutate broker or exchange state is out of scope.

3. **Auditor column required.** Every stock-screener output must include an `auditor: VERIFY` column. This is a compliance requirement for independence-restriction rules and must never be omitted or renamed.

4. **Position-sizing hard cap.** All position-sizing logic must cap risk at 1.5% of account value per trade. Any sizing calculation that would breach that limit must refuse to produce a result and raise an explicit error instead.

## Project Status

Repository is in early setup. Add architecture notes, build commands, and development workflows here once the codebase is established.
