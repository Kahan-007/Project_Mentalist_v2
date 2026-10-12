> Subsequent long-term research improvements are documented in [LONG_TERM_UPGRADE.md](LONG_TERM_UPGRADE.md). The items below describe the initial Telegram implementation.

# Implementation status — 11 October 2026

## Implemented first: correctness and reliability

- Tracked stocks enter **Review data** when analysis or a positive valuation is unavailable, financial history is provisional, essential coverage is insufficient, or dates fail freshness checks. Price freshness currently allows seven calendar days; annual statements allow 450 days. These are configurable-in-code guardrails, not an exchange-calendar implementation.
- Unknown borrowings, interest and cash are preserved as unknown in the affected leverage, coverage, ROCE, ROIC and valuation calculations. Zero reported profit observations no longer earn a perfect no-loss score.
- Growth from a nonpositive starting profit is undefined rather than zero. Positive-endpoint growth rates remain usable independently. This changes DCF assumptions for some companies, including KPITTECH; old targets in the original review are obsolete.
- Cash conversion, capital expenditure/operating cash flow and certain other ratios align numerator/denominator observations to the same years. TTM profit requires four consecutive quarterly dates and four actual profit observations.
- Latest repeated troughs restart the no-new-low clock in the stability test.
- Financial refresh now runs weekly throughout the year. New quarterly data can update an imported annual export without replacing its annual statements. This is a scheduled fallback; earnings announcements do not yet trigger a targeted financial download.
- A transactional SQLite outbox replaces “mark notified, then try to send.” Delivery failures retry per channel and persist across restarts. `daily --no-notify` does not consume event-delivery flags or model-transition state.
- Network backoff is scoped by host. Telegram validates API responses, hides credentials in errors and respects retry hints. Signal history now includes frozen settings with the analysis.

## Implemented second: Telegram and continuous news

- Private, expiring bot pairing from the local dashboard; user/chat authorization; persisted Telegram update cursor.
- Read-only commands and welcome/test messages.
- Separate workers for Google RSS, official NSE announcements, saved model-status changes and delivery. Financial maintenance no longer blocks these workers.
- Source-linked, timestamped headlines with simple materiality triage; original NSE disclosure URL used for deduplication. Exact normalized issuer names prevent incidental mentions from assigning another company's disclosure.
- In-app copies of fresh headlines and model-status alerts.
- macOS login-service installer, weekday daily job, and portable Windows daily launcher.

## Current investment rules

The three weighted philosophy models still use a 65% pass mark, at least 70% core coverage and their essential tests. A tracked stock must first pass the new data gate. Then the existing model uses any matching philosophy and a configurable price/value limit (default 2×):

| Situation after the data gate | Label |
|---|---|
| Held, matches at least one philosophy, within value limit | Hold / In portfolio |
| Held, no philosophy match or above value limit | Sell review prompt |
| Shortlisted, not held, matches, within value limit | Buy review prompt |
| Shortlisted, not held, unmatched or above value limit | Watch |

The 2× limit is **not** a margin-of-safety requirement. A company valued by the model at ₹100 can still fall within that limit at ₹200, subject to the philosophy's other tests. News alerts do not automatically change these labels or execute orders. Price triggers assume unchanged financials and are not forecasts.

The existing general-company DCF remains an approximation. CFO minus capital expenditure is not audited/normalized consistently as FCFF or FCFE across all sources. The correct discount rate and debt/cash treatment depend on that choice; see [Damodaran's explanation of equity versus firm valuation](https://pages.stern.nyu.edu/adamodar/New_Home_Page/lectures/approach.html). This implementation fixes missing inputs but does not claim to resolve the underlying model design or validate its return forecasts.

## Next phases, in implementation order

1. **Intraday price and event analysis:** select an authorized market-data provider; obtain stock and benchmark/sector streams with timestamps, entitlements and exchange calendars; build intraday volatility estimates separately from the daily model. RSS and the current daily Yahoo candles cannot provide this. No provider subscription or broker credentials have been created or purchased.
2. **Valuation and portfolio rules:** normalize FCFF/WACC or FCFE/cost-of-equity inputs, audit source accounting, add scenario ranges; then test distinct entry margin-of-safety and exit thresholds, hysteresis, position limits and sector concentration. Risk limits need the user's investment horizon and risk budget. Do not tune settings to make existing holdings pass.
3. **Historical and forward validation:** collect point-in-time publication dates, historical universes, adjusted prices, costs and slippage; perform walk-forward evaluation and forward paper monitoring. The current ledger/snapshots provide an audit trail, not a backtest or proof of profitability.

Third-party Telegram-channel ingestion, edited-story handling, semantic cross-publisher deduplication, BSE official disclosures, live intraday reactions, order execution and portfolio sizing are not implemented in this increment. Bot membership/permissions and provider access would be separate work.

## Validation

The complete test suite passed 114 tests, including localhost dashboard/API tests. Added checks cover retry/deduplication/concurrent claims, transactional rollback, dry-run event handling, unauthorized/expired/Unicode Telegram messages, token-redacted errors, official issuer mapping/timezone parsing, headline freshness, signal snapshots, missing financial inputs and quarter continuity. The changed calculation/delivery modules were rerun after final adjustments.

Live Google and NSE checks succeeded for the current two-stock watchlist. The installed launchd dashboard is running on port 8766, both source checks succeeded, and the weekday daily job is loaded. Telegram pairing was verified on 11 October 2026. The welcome message and a separate connection-test alert were acknowledged by the Telegram API on their first attempts. Phone notification display and read-only command replies can be checked with /status and /stock KPITTECH; API acknowledgement alone does not establish that the user saw a phone notification. No investment-performance claim follows from these tests.

Source files were backed up before this implementation under `/tmp/mentalist-before-implementation/`. That temporary directory is a convenience backup, not durable version control. SQLite migrations add tables without replacing the existing portfolio/news/ledger tables.
