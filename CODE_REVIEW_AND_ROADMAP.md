> Historical review of the pre-implementation code. Some findings below have now been fixed and example valuations have changed. See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for current behavior.

# Mentalist: code review and next implementation plan

Reviewed 10 October 2026. This describes the code and saved local data, not verified live market prices or recommendations to trade the named stocks. Application code and configuration were not changed.

## Purpose and architecture

The intended product is a personal Indian-equity research and monitoring assistant: find durable businesses at reasonable prices, maintain a shortlist and portfolio, detect unusual price falls around company news, and alert when those falls settle. The implementation combines a fundamental screener with a separate daily event-study engine. It does not place broker orders, learn from outcomes, or currently change fundamental scores in response to headlines.

The Python standard-library backend serves a plain JavaScript dashboard on localhost. JSON stores financial records, settings, caches and the shortlist; SQLite stores trades, news, events, notifications and recommendation snapshots.

| Area | Files | Responsibility |
|---|---|---|
| Entry and operations | `cli.py`, `__main__.py`, `schedule.py`, batch scripts | CLI, local server, Windows daily task |
| Financial inputs | `fundamentals.py`, `workbook.py`, `xlsx.py`, `screener_fetch.py`, `autofill.py` | Imports, derived financial metrics, ownership/NPA/yield refresh |
| Universe and provenance | `index250.py`, `universe.py`, `sources.py`, `verify.py` | Constituents, classification, matching, sources and checks |
| Research decisions | `scoring.py`, `presets.py`, `recommendations.py` | Scores, valuation, price boundaries, signal history |
| Prices and news | `prices.py`, `price_snapshot.py`, `news.py`, `events.py`, `tracker.py` | Daily data, RSS, abnormal returns and event lifecycle |
| Portfolio and delivery | `portfolio.py`, `shortlist.py`, `daily.py`, `notify.py`, `appnotify.py` | FIFO accounting, watchlist, reports and notifications |
| Infrastructure and UI | `config.py`, `net.py`, `storage.py`, `db.py`, `server.py`, `web/` | Settings, HTTP, persistence, APIs, dashboard |

The saved copy has 272 financial records, all score without exceptions. All current-price dates are 8 October 2026. There is no `data/shortlist.json`; the latest report tracks only KPITTECH and TATAPOWER holdings. Importing 272 companies does not subscribe all of them to news monitoring.

## How scores and baselines work

These are hand-written weighted rule scores inspired by three investors, not probabilities of profit or validated reproductions of their investing methods.

For each philosophy:

`score = 100 × sum(weight × criterion score) / sum(available core weights)`

Individual criteria score between zero and one, generally with linear partial credit. Missing core criteria are excluded from the denominator. Munger red flags then subtract points. A match requires an unrounded score of at least 65, core data coverage of at least 70%, and every applicable essential test both known and passed. Fewer than six annual observations only sets a provisional warning; it does not prevent a match or Buy.

General-company criteria and weights:

| Philosophy | Core weights, totalling 100 |
|---|---|
| Buffett | ROE consistency 20; no losses 5; EPS growth 10; debt/equity 15; free-cash-flow conversion 15; capital needs 5; interest coverage 5; margin of safety 15; retained-earnings value creation 10 |
| Munger | Median ROCE 25; ROCE floor 10; operating-margin stability 10; revenue growth 10; EPS growth 10; FCF yield relative to G-Sec 20; debt/equity 15 |
| Lynch | Growth-plus-yield/P/E 25; growth range 15; EPS consistency 10; promoter ownership 10; institutional ownership 5; size 5; debt/equity 10; inventory growth 10; net cash 10 |

Munger deducts up to 10 points for pledging, 8 for dilution, 8 for weak cash conversion, 6 for receivables outrunning revenue, 6 for other-income dependence and 4 for low effective tax. Missing penalty inputs cause no deduction and do not reduce reported core coverage.

The effective essential thresholds for general companies are:

| Philosophy | Essential conditions, in addition to overall score and coverage |
|---|---|
| Buffett | ROE at least 15% in at least 55% of available observations within the last ten; latest available debt/equity at most 1.0; margin of safety at least zero |
| Munger | Median ROCE at least 17.5%; minimum available ROCE within the last five observations at least 10% |
| Lynch | `(EPS growth % + dividend yield %) / P/E` at least 1; debt/equity at most 0.675 |

For cyclicals, Lynch replaces the growth valuation test with price divided by median historical EPS: full marks at P/E 10 or lower, zero at 25 or higher, essential threshold 20 or lower. Interest coverage replaces the growth-range criterion. Cyclical classification uses industry when history is short, or repeated EPS declines; category changes can change the valuation method.

Financial businesses use different criteria. Buffett substitutes ROA, gross NPA, book-value growth and justified P/B; insurers substitute premium growth for ROA/NPA. Munger's essential financial-company thresholds are median ROE at least 14% and a five-observation ROE floor at least 6%. Banks/NBFCs require gross NPA at most 5% for all three philosophies. Insurers currently share much of the lender valuation approach, which needs refinement.

### Value baseline

For general companies, `dcf_value()` starts with three-year average FCF, or historical median FCF for cyclicals. FCF is CFO minus reported capex where available, otherwise estimated capex from changes in net block and CWIP plus depreciation. It forecasts ten years, using the lower available long-run/five-year EPS CAGR, floored at zero and capped at 15%. It adds a terminal value, subtracts debt and adds cash.

The local settings currently imply a 10.3% discount rate: stored G-Sec yield 7.3% plus a 3% premium. This is a configuration observation, not verification of today's bond yield. Terminal growth is 5%, subject to a two-percentage-point gap below the discount rate.

For financial firms, value is book equity multiplied by `(median ROE − growth) / (cost of equity − growth)`. The financial-company discount rate includes one additional percentage point, currently 11.3%; growth depends on ROE, payout and book-value growth, subject to a cap.

`margin of safety = 1 − market capitalisation / estimated equity value`

Buffett gives full valuation credit at a 25% margin of safety, but its essential gate only requires zero. The estimate is a model output, not a demonstrated conservative lower bound.

### Buy, Hold, Sell and price triggers

`recommendations.assess()` implements:

| Situation | Label |
|---|---|
| Held, matches at least one philosophy, does not exceed value limit | Hold, displayed as In portfolio |
| Held, matches none OR exceeds value limit | Sell |
| Shortlisted, not held, matches at least one, does not exceed value limit | Buy |
| Shortlisted, not held, no match OR exceeds value limit | Watch |
| Neither held nor shortlisted | No signal |

The value limit is **2× estimated value**, not a 2% threshold and not a requirement to buy below fair value. Equality is allowed; strictly greater fails. One matching philosophy is sufficient. Missing/nonpositive valuation produces no multiple and bypasses the price check. Purchase cost, news, event state, portfolio concentration and stop-loss levels do not enter this decision.

`price_headroom()` rescales price and market cap while leaving financial statements unchanged. It searches down to 20% and up to 1,000% of the current price, using bisection to find changes in matches, then applies the valuation cap. These are conditional rule boundaries, not price forecasts. Price-dependent category changes mean monotonicity should be tested before relying on every bisection result.

Examples recomputed from the saved 8 October inputs:

| Stock | Saved price | Buffett / Munger / Lynch | Current model result |
|---|---:|---|---|
| KPITTECH | ₹471.00 | 43.3 / 65.0 / 78.4 | Sell: matches Munger and Lynch, but market value is 4.09× estimated value. Price-discipline boundary about ₹230.52, all else equal. |
| TATAPOWER | ₹335.65 | 59.8 / 38.8 / 48.9 | Sell: no philosophy match. The search finds no qualifying price within an 80% fall because quality/balance-sheet gates fail. |

KPITTECH also illustrates valuation sensitivity: its five-year EPS growth is about 34.3%, but the longer-span metric is zero because a nonpositive starting EPS is encoded as zero growth. Taking the lower of the two makes the ten-year DCF growth assumption zero. That boundary needs review of the underlying history; it should not be treated as an independently reliable trade target.

## News and event baseline

Google News RSS headlines are filtered by company names/tickers/aliases and labelled by keywords. There is no article-level materiality or sentiment model. Exact normalized titles are deduplicated, and all headlines for a company in one trading session share an event. Thus positive news and an unrelated falling price can appear in the same event.

The event engine fits daily log stock returns against Nifty 50 log returns:

`expected return = alpha + beta × market return`

It uses up to 120 estimation sessions, requires 60, and excludes a five-session gap before the event. It sums residual returns over the event day and next session. With n available event sessions, a dip is confirmed when:

`CAR <= −max(−ln(0.97), 2 × residual_sigma × sqrt(n))`

This is approximately a drop of at least 3% beyond expected return and at least two residual standard deviations scaled for the window. A sufficiently large first-session drop can confirm early. Volume above 1.5× its estimation-period median is contextual, not required.

Levelling needs five sessions after the two-session event window, recent five-session average true range no more than 1.25× the pre-event 14-session average, abnormal drift within one residual sigma times √5, and the post-event trough outside the latest three sessions. It does not establish a rebound or cause a Buy. Equal lows deserve a regression test: the minimum tuple selects the first occurrence of a repeated trough.

Today's candle is excluded until 16:00 IST. After-close headlines map to the next trading session. The default installed task runs weekdays at 18:30. The dashboard news cache lasts 30 minutes. Repeating the daily job every minute would retain the daily-data limitation and repeatedly run expensive maintenance.

## Telegram and faster reactions

### Existing capability

`notify.py` already calls Telegram `sendMessage`. Configure the bot token and chat ID under dashboard Settings → Alerts, or use `SCREENER_TELEGRAM_TOKEN` and `SCREENER_TELEGRAM_CHAT` in the worker's environment. Both are currently absent. Create the bot through BotFather, start its private chat, obtain that chat's ID from a bot update, save the values locally, then use the existing test-alert button. The test command is `python3 -m india_screener notify-test` on this Mac, or `py -m india_screener notify-test` on Windows. No message was sent during this review. See [Telegram's setup tutorial](https://core.telegram.org/bots/tutorial).

This enables delivery of existing dip/levelling alerts only. Fresh headlines are not individually pushed. Buy/Sell changes are saved to the recommendation log, not sent through `notify.send()`.

### Proposed implementation

Use a separate always-running worker with this flow:

`news/Telegram input → durable inbox → deduplication and company mapping → event assessment → durable alert outbox → Telegram`

1. Start with official exchange announcements and Google RSS as a secondary discovery source. NSE publishes [corporate-information RSS](https://www.nseindia.com/static/rss-feed) and a separate [corporate-data subscription](https://www.nseindia.com/static/market-data/corporate-data-subscription). Feed selection must include measured availability and latency; a subscription alone is not proof of a streaming SLA.
2. Poll shared announcement feeds initially at 30–60 seconds where permitted, with jitter, adaptive backoff and persisted cursors. Prioritize holdings and shortlisted companies. Do not make 250 independent Google searches every few seconds.
3. Immediately send a labelled “new material news; price impact unconfirmed” alert. Include source link, publication/receipt time, stock, holding status, data age and current model context. The present alert formatter omits headline URLs even though they are stored.
4. For Telegram commands and forwarded stories, use `getUpdates` long polling on the local machine; it needs no public inbound endpoint. Add read-only `/status`, `/stock SYMBOL`, `/news SYMBOL` and `/portfolio`, authorized to explicit user/chat IDs. Persist update IDs and handle edits. For a hosted deployment, HTTPS webhooks are an alternative with secret-header verification. Telegram documents both approaches in the [Bot API](https://core.telegram.org/bots/api); they are mutually exclusive for receiving updates.
5. If “Telegram news” means third-party channels, bot access is a separate requirement. Bots receive posts from channels where they are members; a bot token is not a general subscription to arbitrary channels. Use approved channel membership or forwarded messages initially. See [Telegram's FAQ](https://core.telegram.org/bots/faq).
6. Add a separate intraday price adapter if rapid price-reaction analysis is needed. Store quote timestamps and distinguish delayed, stale and live data. Fit an intraday market/sector model with time-of-day volatility; do not reuse daily sigma against minute returns.
7. Keep financial/ownership refresh and final daily event confirmation in scheduled background jobs. Notify on material signal transitions through the same outbox.

Target under five seconds from receipt to alert enqueueing for cached analysis. This is an engineering acceptance target, not measured performance. End-to-end delay is source publication delay + ingestion delay + processing + Telegram/network delivery. No design based on periodic RSS can promise immediate awareness of the original event. A sleeping/offline laptop cannot monitor continuously.

## Prioritized algorithm and reliability changes

| Priority | Finding | Next change |
|---|---|---|
| P0 | Missing analysis can display Sell for a held stock; missing value can permit Buy. Seven saved records match a philosophy without a positive valuation. | Add explicit insufficient-data, stale-data and valuation-unavailable states. Require valid inputs for actionable entry labels; distinguish business deterioration from missing evidence. |
| P0 | Tracker marks alerts notified before delivery; failed delivery or `--no-notify` consumes the notification flags. | Transactionally enqueue an outbox item with the event update; track delivery per channel and retry after restarts. Handle ambiguous send outcomes without claiming exactly-once delivery. |
| P0 | Daily maintenance blocks news processing; all stock work finishes before sends. HTTP backoff is shared across hosts. | Separate fast ingestion/notification workers, consume completed work immediately, and scope circuit breakers/backoff by provider. |
| P0 | HTTP errors can contain the Telegram URL, including its token; Telegram response bodies are not checked. | Redact credentials, validate `ok` and delivery IDs, handle retry hints, and check both token and chat ID in configuration status. |
| P1 | DCF uses CFO−capex without explicitly normalizing financing cash flows, a fixed equity-style discount rate, then subtracts debt. | Choose an internally consistent FCFF/WACC or FCFE/cost-of-equity model; audit interest classification, cash and debt adjustments. Add bear/base/bull assumptions and show sensitivity. |
| P1 | Results refresh is limited to May/June/November/December, and existing exports can reject newer quarters until a newer annual year arrives. | Refresh on earnings announcements plus a year-round fallback; merge quarterly and annual data independently with provenance. |
| P1 | Provisional history and stale financials do not block signals. Missing debt can become zero; missing interest can become infinite cover; no reported profit observations can earn “no losses” credit. | Preserve unknown values, introduce a data-quality gate, require chronological/as-of validation, and align ratio numerators and denominators to identical periods. |
| P1 | A 2× value cap and any-one-philosophy rule mix quality eligibility with entry/exit decisions. | Separate quality, valuation, event risk and portfolio risk. Test a true entry margin of safety, distinct exit criteria, and hysteresis to prevent repeated flips around one threshold. |
| P2 | Insurers, lenders, holding companies, utilities and growth firms need different economics. | Introduce sector-specific models and peer context; add lender capital/asset-quality measures and insurer-specific operating/value measures when sourced reliably. |
| P2 | One daily market benchmark and keyword-only headlines cannot establish company-specific event impact. | Add sector-relative returns, volatility adaptation, materiality and source confidence; retain distinct underlying events and linked updates instead of merging everything by session. |
| P2 | No position sizing, concentration or liquidity rules enter Buy/Sell. | Add configurable single-stock/sector exposure, liquidity checks, entry tranches and portfolio-level risk reporting. |
| P2 | Tests verify implementation, not profitability. | Build point-in-time walk-forward evaluation including historical universe membership, publication times, corporate actions, costs, slippage, turnover, drawdowns and benchmark-relative returns. Paper-monitor before assigning financial significance to new thresholds. |

The cash-flow consistency recommendation follows the distinction between equity and firm valuation in [Damodaran's valuation framework](https://pages.stern.nyu.edu/adamodar/New_Home_Page/lectures/approach.html). It does not imply that every current valuation is biased in the same direction; source accounting treatment must be audited.

Additional engineering items: remove the machine-specific Windows interpreter path in `run_daily.cmd` or regenerate it during setup; provide macOS/Linux service setup; consolidate shared analysis outside `server.py`; use one writer or transactionally versioned records for concurrent refreshes; record algorithm/settings versions with recommendation snapshots; validate settings ranges; make ledger deletion preserve replay validity; replace hardcoded workbook retrieval dates with actual metadata.

Implementation order: (1) signal/data correctness and outbox reliability, (2) continuous news worker plus Telegram delivery/commands, (3) intraday price and event analysis, (4) revised valuation and portfolio rules backed by historical and forward evaluation. Avoid tuning thresholds merely to make the current holdings pass.

## Validation performed

Read the backend modules, dashboard code, launchers, configuration and tests; inspected stored records and the latest report; independently recomputed scores and price boundaries from local data. The initial suite executed 88 tests successfully but the dashboard class could not bind its localhost socket in the sandbox. Rerunning the dashboard module with local-server permission passed its 10 tests. These checks establish implementation behavior, not investment performance or live provider availability.
