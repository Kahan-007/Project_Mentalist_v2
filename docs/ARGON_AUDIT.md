# Argon reference audit

All 32 `gemini-code-*.py` files were compared with the existing implementation. **None was copied wholesale, imported at runtime, used as financial input, or used to train a model.** They are alternative source-code proposals, including competing versions of the same modules. The application still calculates explicit rules and formulas.

“Adapted” below means selected ideas were implemented against the current code with regression coverage; it does not endorse the rest of that file. A rejected replacement can contain a useful isolated idea. The original reference files remain outside this branch.

| File suffix (after `gemini-code-`) | Intended module | Decision and reason |
|---|---|---|
| 1791975648416.py | portfolio | Adapted fractional-entitlement handling and replay integrity. Rejected rounding away entitlement without actual settlement proceeds; implemented proportional disposed basis and atomic mutations. |
| 1791975662373.py | portfolio tests | Used as a scenario reference only. Wrote independent rollback, settlement, cost-basis and concurrent-writer tests; did not accept tests that bless silent entitlement loss. |
| 1791975666781.py | events | Adapted missing-data/stabilization checks. Kept the current latest-tied-low rule; no wholesale replacement. |
| 1791975674240.py | tracker | Adapted evaluation ordering: confirm newer dips before older events can level. Preserved transactional outbox delivery and notification controls. |
| 1791975679349.py | news | Adapted ticker disambiguation/earliest publication ideas. Retained official NSE disclosure ingestion. |
| 1791975690714.py | prices | Adapted narrow zero-volume carry-forward detection. Retained instrument, nonfinite and adjusted-price validation. Legitimate low-volume trading is not discarded. |
| 1791975707121.py | daily | Adapted closed-position value and absent z-score handling. Retained the current recommendation/queue pipeline. |
| 1791980121599.py | db | Rejected replacement: incomplete schema initialization and path caching could omit newer subsystems. Implemented database-resident versions, additive transactional migration and WAL instead. |
| 1791980127370.py | storage | Adapted bounded PermissionError retries for atomic replacement, with cleanup and preservation on failure. |
| 1791980131692.py | test fragment | Not imported: this is an incomplete standalone test fragment. Independently tested operating ROIC/ROCE with explicit numeric examples. |
| 1791980136843.py | sources | Adapted removal of a fixed retrieval date and corrected metric descriptions. |
| 1791980144628.py | prices, competing version | Rejected as a replacement. Selected validation ideas overlap the earlier proposal; retained all current quote and instrument protections. |
| 1791980150120.py | news, competing version | Adapted earliest-timestamp/name-context ideas only. Rejected regressions to NSE support and unreliable capitalization checks. |
| 1791980159407.py | tracker, competing version | Adapted two-pass event evaluation only. Rejected removal of persistent delivery and no-notify safeguards. |
| 1791980169814.py | events, competing version | Adapted post-event trough guard and stricter alignment. Retained repeated-low behavior already covered by existing tests. |
| 1791980185665.py | scoring | Rejected replacement: it removes data-quality, entry and sensitivity safeguards. Adapted latest-observation semantics; new conservative scoring and cyclical policy are explicitly experimental. |
| 1791980195002.py | fundamentals | Rejected replacement: missing facts must not become zero; missing interest is not infinite coverage; loss-base CAGR is not zero; no arbitrary capital floor or negative-capex clamp. Adapted operating-income separation with stricter missing-data handling. |
| 1791994107349.py | workbook | Adapted price-date fixes and safer share evidence. Rejected historical share backfill from unchanged capital and unconditional profit-for-EPS substitution. |
| 1791994128312.py | screener_fetch | Adapted preservation of omitted schedule cells. Added retention of older annual periods and quarterly union; kept newer exports, basis checks and manual entries. |
| 1791994140160.py | autofill | Adapted total-failure detection. Rejected restricting financial refreshes to results-season months; retained year-round refresh and added explicit failed/partial retry state. |
| 1791994151095.py | price_snapshot | No change adopted. Retained the existing NSE 17:00 readiness gate and holiday fallback; did not move readiness to 16:00. |
| 1791994158238.py | verify | Adapted “skip” when cash-flow reconciliation has no complete comparable rows, instead of reporting an unperformed test as successful. |
| 1791994164122.py | recommendations | Rejected replacement: review gates, confirmations and transactional evidence must survive. Added comparison and prospective capture to the existing implementation. |
| 1791994170337.py | server | Adapted generic-name filtering for import matching. Rejected removal of Telegram, monitor, quality and validation endpoints. Added narrow validated endpoints independently. |
| 1791994197661.py | net | Rejected replacement: global backoff and full URL errors regress isolation/secrecy. Retained per-host backoff/redaction; added bounded gzip, finite Retry-After and response cleanup independently. Cookie-session warmup deferred. |
| 1791994209892.py | xlsx | Adapted bounds, sequential omitted references and unavailable formula-error cells. Did not adopt the complete parser replacement or add rich-text/phonetic parsing changes. |
| 1791994215020.py | shortlist | Not adopted in this upgrade. Metadata preservation on repeated add is a separate small follow-up; existing tested shortlist behavior retained. |
| 1791994221438.py | appnotify | Not adopted. Report-filename filtering remains a follow-up; wholesale schema removal would conflict with the existing module contract. |
| 1791994227126.py | schedule | Not adopted. Windows launcher/start-date changes need platform-specific validation; current scheduling retained. |
| 1791994232806.py | notify | Not adopted. Hidden PowerShell launch is optional UI behavior and does not justify replacing validated delivery code. |
| 1791994240751.py | daily, competing version | Only the same small report fixes above were adapted; did not replace the notification pipeline. |
| 1791994246569.py | cli | Not adopted. Cosmetic cash display does not justify dropping existing monitor/service commands. |

The cyclical history-length defect was found by tracing the existing scoring code, not by indiscriminately applying these references. New work also includes explicit FCFF/FCFE scenarios, prospective research metadata/cohorts, source health and unchanged-holdings risk reporting; those are integrations into the existing application.
