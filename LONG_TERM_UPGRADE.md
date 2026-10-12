# Long-term investing with fast news alerts

Implemented on 11 October 2026. The dashboard remains at http://127.0.0.1:8766/ and the new view is **Monitor & risk** (`#quality`). Existing portfolio transactions and investment thresholds are preserved. Research model version: 2.1.

## New capabilities

| Area | Implemented behavior |
|---|---|
| Source health | Separate healthy/error/stale/not-checked states for Google, NSE and model checks. A fresh process heartbeat no longer implies every source is healthy. |
| Alert audit | Saves first monitor detection, source publication timestamp, queue identity, triage explanation, and stale/future/invalid/queued disposition. Delivery acknowledgement remains per channel. New schema is additive. |
| Latency | Seven-day sample counts, median and nearest-rank 95th percentile for publication-to-detection and queue-to-Telegram acknowledgement. Empty samples remain empty; old alerts receive no invented timestamps. |
| Feedback | Rate headline alerts useful/noise/incorrect/duplicate from the dashboard or `/feedback ID rating`. Changing a rating updates that one local assessment. No automatic parameter tuning. |
| Materiality | Distinguishes routine filing language, potential thesis events and urgent credit/governance language. Denial/allegation language qualifies an urgent label. Source type distinguishes NSE disclosures from secondary reporting. |
| Order context | A single explicit INR order/contract amount is converted to crore and compared with the latest annual revenue only when dated within 450 days. Ambiguous multiple amounts, foreign currency and stale revenue are not used. The ratio is context, not incremental annual revenue or profit. |
| Concentration | Saved-price position weights and industry exposure; stale/missing prices and missing classifications are visible. Optional limits are warnings only. Cash, other accounts and unrecorded assets are excluded. |
| Valuation sensitivity | A stock's analysis shows estimates at the current required return and at up to two percentage points higher/lower. Existing growth caps still apply. These are assumption sensitivities, not calibrated probability scenarios. |
| Entry discipline | Optional `entry_margin_of_safety`, -1 for existing behavior or 0–0.8 for a required discount. It applies to unheld shortlist entries; held-stock exit rules remain separate. The default stays -1. Preset undo/restore supports this field, including older presets. |
| Valuation worksheet | Explicit FCFF/WACC or FCFE/cost-of-equity inputs, ten forecast years and terminal value. FCFF subtracts debt and adds surplus cash; FCFE does not deduct debt again. Positive normalized cash flow/shares and terminal growth below the discount rate are required. Results do not alter signals. |
| Research archive | Immutable observations include actual observation time, financial statements, scoring settings, position/shortlist state, algorithm version and input hash. Repeated identical inputs within a day are deduplicated. Input changes create a new observation. |
| Forward outcomes | Observations use the first benchmark session close strictly after the observation date, then 20, 60, 120 and 252 subsequent sessions. Stock/benchmark adjusted closes must cover the whole window. Shows gross stock return, benchmark return, their difference and maximum drawdown. |

## How to use it

1. Open **Monitor & risk**. Check source states and price coverage before interpreting financial outputs.
2. Verify portfolio quantities and classifications. Set warning limits only after deciding how much of the recorded equity portfolio you are comfortable concentrating in one stock or industry. Zero means off; no limit has been chosen for you.
3. Rate actual news alerts. Review the originals before calling a headline incorrect. A useful report need not cause an immediate share-price move.
4. On **Analyse a stock**, expand valuation sensitivity. If a modest change in the required return greatly changes estimated value, treat the single-number valuation cautiously.
5. Use the FCFF/FCFE worksheet only with reviewed accounting inputs. It provides a consistent calculation, not verified inputs. The screening model is still a proxy and is clearly labelled as such.
6. Let the forward archive accumulate actual observations. Cached histories refresh through the daily job. Outcomes appear only when the required subsequent sessions exist; the archive does not reconstruct unrecorded historical predictions.

The financial distinction between equity and firm cash flows follows [Damodaran's valuation framework](https://pages.stern.nyu.edu/adamodar/New_Home_Page/lectures/approach.html). The worksheet makes this distinction explicit rather than assuming that every imported CFO-minus-capex number is normalized FCFF.

## Important interpretation limits

- A source's publication timestamp can lag the real event or be revised. Telegram acknowledgement is not the time a phone displays a notification. Missed stories require an independent reference feed to measure; the app cannot infer them from its own received stories.
- Keyword triage cannot resolve all negation, rumours, amendments, subsidiaries or financial context. It does not inspect full PDF attachments. Semantic cross-publisher deduplication and targeted earnings-document extraction remain future work.
- Weights are for valued recorded equities. Limit flags are disabled if any recorded holding has stale/missing prices. Industry flags are also disabled when classification is incomplete. Industry exposure is not a full covariance or stress model.
- Entry and exit thresholds remain hand-written rules. The optional margin-of-safety threshold is a configurable research control, not a proven optimal level. Existing headroom calculations are hidden for an unheld stock when this new entry rule is enabled, to avoid presenting a legacy boundary as its entry price.
- The active screening DCF still lacks audited cash-flow normalization and company-specific calibrated discount rates. The consistent research worksheet does not silently replace it. Sector-specific insurer/lender models and full scenario forecasting remain separate work.
- Forward outcomes are descriptive close-to-close returns, not executed or executable trades. They exclude costs, taxes, slippage and portfolio construction. Daily observations overlap; do not treat them as independent trials. Current cached histories may contain provider revisions. Missing baseline/entry/window data remains unavailable instead of shortening the horizon or substituting a benchmark ETF.
- Intraday data and automatic trading are not added. They are not prerequisites for this chosen long-term workflow. Live data would require a suitable provider; [NSE describes its real-time products and authorized-vendor route](https://www.nseindia.com/static/market-data/real-time-data-subscription).

## Engineering checks

Tests cover transaction rollback, headline timestamps and future retry, deduplication, source degradation, feedback validation, mixed-currency/ambiguous order headlines, position weights with missing prices, frozen observations, future-horizon availability, aligned adjusted returns, drawdown, entry/exit separation, historical preset compatibility, FCFF/FCFE algebra, invalid inputs and JSON-safe API output. All 272 imported records and their valuation sensitivities were recomputed successfully. The existing price-headroom path also received a regression fix for misplaced date-validation code.

A pre-change backup of code/tests and the SQLite database is at `/tmp/mentalist-before-quality-upgrade/`; credentials were excluded. This is a temporary rollback copy, not durable version control.


Final verification: **132 tests passed**. JavaScript syntax and Python compilation passed. After restarting the installed service, the live localhost APIs confirmed healthy Google/NSE/model checks, retained Telegram pairing, two forward observations, 272 score rows without errors, and the expected FCFF worksheet result. `/quality`, `/risk` and `/status` command handlers returned valid responses. Latency collection has started; no synthetic headline was inserted into the live audit to fabricate a sample. Browser visual inspection was unavailable because the browser connector could not start; the frontend was checked for syntax and its backing APIs were exercised.
