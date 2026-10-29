# Mentalist reliability and valuation review

Base: `7440f87`. Review branch: `codex/mentalist-reliability-valuation-upgrade`.

This upgrade is prepared separately from the running application and production branch. It does not activate an experimental valuation or cyclical policy for automatic alerts. No private configuration, financial imports, portfolio database or Argon reference files are included in the change.

## What changed

| Area | Before | Implemented behavior |
|---|---|---|
| Portfolio ledger | Some mutations could commit before complete replay; fractional entitlements rejected | Add, correction, deletion and entire broker imports use one immediate transaction and validate complete replay before commit. Audit records preserve before/after values. Actual cash settlements support fractional split/bonus entitlements. |
| Persistence | Schema initialization repeated without a database version; transient file locks could fail atomic writes | Additive SQLite migration with `user_version`, WAL and transaction boundaries. Bounded atomic-replace retries preserve the previous file on failure. |
| Refresh | Failed/partial work could look successful; shorter provider responses could lose evidence | Per-job attempt/status/last-success/retry state; total/partial failure distinguished; retry after one day; year-round financial refresh retained. Annual history, omitted cells, richer exports, quarterly details, manual entries and newer price dates preserved. |
| Price data | Flat holiday placeholders, array discrepancies and fallback provenance could be unclear | Instrument/adjustment validation, sorted timestamps, conflicting duplicate rejection, narrow flat zero-volume carry-forward exclusion, and explicit stale-fallback status sidecars. |
| Financial data | Some missing facts could be replaced by older observations or implicit zero assumptions | Latest-period leverage/book evidence; actual elapsed-period CAGR; explicit share provenance; four contiguous completed quarters for TTM; missing capex/dividends remain unknown. |
| Valuation | Existing approximate screening DCF and standalone worksheet | Existing valuation labelled legacy. Reviewed per-company FCFF/FCFE inputs, conservative/base/optimistic sensitivity, terminal dependence and missing-input explanations added separately. |
| Model interpretation | Legacy scores rescale available tests | Both legacy and conservative scores exposed, with passed/failed/unknown states, unknown red flags and freshness. Experimental operating/cyclical comparison does not enter live notification transitions. |
| Cyclicals | Industry classification only forced below seven observations | Both classifications and the same mid-cycle diagnostic displayed. Experimental sector-first policy applies regardless of history length. Historical window and limitations are explicit. |
| News and events | Common-word ticker matches and duplicate timestamps could distort relevance/timing | Ambiguous ticker context/aliases, earliest trustworthy duplicate timestamp, preserved NSE IDs, stricter event/benchmark alignment, newer-dip supersession and post-event trough protection. |
| Research | Frozen forward observations but limited outcome metadata and aggregation | Frozen benchmark, model, actual confirmed signal, input vintage, quality and price evidence; 20/60/120/252-session outcomes, costs, coverage, distributions and overlap-aware cohorts. |
| Portfolio risk | Concentration and saved-price issues | Position price coverage plus returns, benchmark comparison, drawdown and volatility for the unchanged-holdings interval after the latest transaction. Unavailable history is explicit. |
| Dashboard and alerts | Existing dashboard/Telegram service | Existing screens extended with evidence, model comparison, reviewed scenarios, cyclical warning, job/delivery health and correction/settlement controls. Alerts add version, valuation assumptions and changed criteria. |
| Boundaries | Existing localhost/credential safeguards | Preserved safeguards; added bounded gzip expansion, finite retry delay, HTTP stream cleanup, spreadsheet grid bounds and correction-field allowlist. |

## Every financial formula or decision change

Production policy remains the legacy philosophy/valuation policy, versioned `2.2-safety`; this does **not** mean every numeric output remains identical. Corrected missing-data semantics can change scores, valuations, matches and eligibility. Thresholds, user preferences, entry-margin setting and price-discipline setting are not rewritten.

1. **Displayed operating ROIC**: EBIT is explicit operating EBIT, or PBT + interest − separately reported other income when all components exist. NOPAT = EBIT × (1 − observed effective tax rate). Invested capital = equity + debt − cash; denominator is the average of two consecutive positive annual bases. Missing tax, observed rates outside 0–40%, nonpositive bases and numerical singularities yield unknown. There is no statutory-rate guess or economic capital floor. Financial institutions are inapplicable. Excluding all cash and classifying all other income as non-operating remain disclosed approximations.
2. **Experimental ROCE and operating margins/cover** use that operating EBIT. ROCE denominator is average consecutive equity + debt. Production ROCE/EBIT scoring retains its previous definition. Experimental model version is `operating-cycle-score-v1`.
3. **Capex/FCF**: reported nonnegative capex preferred. Otherwise capex = change in net block + change in CWIP + depreciation, only with both CWIP observations. Negative estimates are unavailable, not clamped to zero. FCF = CFO − capex. Each period identifies reported/estimated/unavailable evidence. This affects legacy FCF conversion, valuation and cash-related criteria where prior assumptions were invalid.
4. **Retained earnings**: the market-value-added test requires every profit and dividend observation in its window; missing dividends are not zero.
5. **CAGR**: endpoints use elapsed calendar months / 12, not array-index distance. Existing minimum evidence and loss-base behavior remain; growth from a nonpositive base is undefined.
6. **TTM**: exactly four finite actual profits, ordered distinct dates, consecutive three-month periods and completed reporting months. Optional period-length metadata must say three months. Missing/malformed sequences are not estimated. If unsuitable quarterly data is present, eligibility requires review.
7. **Shares**: latest reported current count preferred; same-period count or capital/face value may establish outstanding shares. Historical EPS denominators stay separate, with weighted-average inference labelled. No fallback to an older annual share count or current face value backfill across corporate actions. This can affect repricing and market-cap-based ratios.
8. **Latest evidence**: debt/equity and current book value do not silently use older known values. Missing debt/cash cannot create net-cash credit or a DCF equity estimate. Positive current price, market cap and current equity/financing evidence are checked. Existing freshness, essential-criterion and provisional-history gates remain.
9. **Conservative score (experimental)**: earned core points / all core weights, less known penalty deductions and the full possible deduction for unknown red flags. Legacy score still divides by available core weights. Removing evidence cannot improve the conservative score. This is a cautious policy proposal, not an empirically calibrated predictor.
10. **Cyclical classification (experimental)**: the existing industry flag takes priority regardless of annual-history length. Other legacy classification logic is retained. Median EPS includes loss years and at most ten annual observations; at least three are needed for an estimate. Normalized P/E = price / median EPS. Thresholds unchanged: full credit at ≤10, zero at ≥25; the essential test needs ≤20. Nonpositive median earns zero; insufficient inputs are unknown. Both history length and legacy omission are displayed. The classification also makes the experimental legacy-DCF comparison use median FCF rather than three-year average FCF. Broad industry tags and full-cycle coverage still require review.
11. **Research valuation**: FCFF = operating EBIT × (1 − tax) + depreciation − capex − change in noncash working capital. Discount FCFF at WACC, then subtract debt and add surplus cash once. FCFE = net income + depreciation − capex − change in noncash working capital + net borrowing; discount at cost of equity, without another debt deduction. Ten explicit years plus terminal value CF₁₁/(r−g). The worksheet existed before this branch; reusable component calculation, reviewed saved inputs and scenarios are new. No automatic CFO-to-FCFF conversion is claimed.
12. **Scenarios and assessment (experimental)**: default sensitivity changes growth and required return by opposing two-percentage-point shifts; explicit custom cases are accepted by the core. A 25% base-case margin-of-safety hurdle labels the experimental purchase assessment. It is a declared, unvalidated research assumption. A disagreement on an existing holding yields review, not an experimental Sell. Missing/rejected inputs yield insufficient evidence.
13. **Research returns**: entry at the first benchmark session strictly after observation day; horizon ends after the stated number of subsequent completed sessions. Require matching stock history throughout. Net stock return = (1 + gross return) × (1 − c)/(1 + c) − 1, where default one-way cost c = 10 bps. Report net excess versus the chosen benchmark; the benchmark itself has no transaction-cost deduction. This measures prospective observations, not actual fills or strategy P&L.
14. **Fractional settlements**: pooled post-action fractional quantity is settled only with user-provided actual positive proceeds. Its fraction of post-action total quantity disposes of the same fraction of original cost basis; retained lots preserve remaining cost. Example: 3 shares costing 1,200, a 1:2 bonus and 150 cash proceeds produce 4 retained shares, 1,066.6667 retained basis and 16.6667 realized profit. Decimal arithmetic is retained; no settlement amount is guessed.

Cash-flow definitions follow [Damodaran's financial definitions](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/definitions.html) and the [CFA Institute free-cash-flow valuation reading](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/free-cash-flow-valuation). The distinction between weighted-average EPS denominators and current outstanding shares follows [IAS 33](https://www.ifrs.org/issued-standards/list-of-standards/ias-33-earnings-per-share.html/). These sources support definitions, not the predictive performance of Mentalist's thresholds.

## Cyclical defect and what the fix does

The legacy classifier checks industry only when `n_years < 7`. With longer history, it instead needs at least two EPS declines exceeding 20% in the last six available observations to label a cyclical, unless another category takes precedence. The result can depend on import length: similar commodity businesses can receive PEG/growth tests versus normalized-P/E/downturn tests. The same branch affects three-year average versus cycle-median FCF valuation.

The experimental version removes that history-length exception for industry-flagged companies and shows the cycle diagnostic alongside legacy output. It does not remove the penalty just because a sector is cyclical: the purpose is to avoid valuing temporarily high earnings as permanent. Different businesses can still deserve different results under identical rules. A median of six years is not equivalent evidence to a median of ten; neither guarantees a complete commodity cycle. Do not retrofit missing earnings or use an arbitrary sector adjustment to equalize scores.

The refresh fix prevents a short new response from erasing previously stored annual periods. Preserved observations retain provenance. It cannot recover data already lost, establish why a particular existing import is short, or automatically reconcile historical restatements/share adjustments.

## Compatibility and migration

- Existing portfolio, recommendation, news/event, outbox/delivery, notification, feedback, signal-evidence and research rows are preserved. Migration tests compare existing table contents before and after repeated initialization.
- SQLite schema version 2 adds research context; version 1 introduced ledger audit. Old observations without a frozen benchmark remain explicitly unevaluable instead of borrowing today's benchmark retrospectively.
- Telegram token, destination, owner pairing, group configuration, commands, deduplication and retries remain intact. No real Telegram sends were used in validation. Experimental scoring is rejected by the production transition logger.
- Existing imports/settings stay supported. Accounting-basis conflicts are rejected for review. Undated workbook source snapshots remain undated; import time is not misrepresented as market-data time. Supply a trustworthy snapshot date where available.
- There is no experimental-live switch in this release. Promotion requires a separate reviewed implementation and explicit opt-in; there is no hidden activation through saved scenario inputs.
- Before eventual deployment, preserve a consistent SQLite backup and configuration. Do not expect an old executable to understand newly entered fractional settlements merely because the schema is additive. Reverse deployment requires checking those newer records; do not blindly downgrade a database with new activity.
- Python remains standard-library-only. Tests ran on macOS/Python 3.14; Windows-specific launches/toasts were not manually exercised. No new framework or paid service is required.

## Validation

Baseline: **154 tests executed; two date-sensitive alert-audit failures** under the current clock. Tests were freezing the monitor date but not the quality-report clock. The clock fixture was corrected; production behavior was not changed to conceal those failures.

Final suite: **199 tests passed, zero failed**. An earlier 211-execution run included redundant inherited ledger tests; those duplicates were removed, and two additional migration/concurrency tests were added. The final count includes 45 additional test executions relative to baseline.

Commands run from the review checkout:

```sh
/Users/siddhantbudhia/mentalist/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
/Users/siddhantbudhia/mentalist/.venv/bin/python -m compileall -q india_screener tests
node --check india_screener/web/app.js
git diff --check
```

The unittest command is the requested `python -m unittest discover -s tests -p "test_*.py"`, using the available virtualenv interpreter because bare `python` is not on the shell path.

Coverage includes existing Telegram owner/group commands, quote confirmation and rollback; synthetic import → score → shortlist/holding → price/alert workflows; scenario-save and correction endpoints; independent ROIC/valuation/settlement examples; contiguous and invalid quarters; provider failures and placeholders; ambiguity and timestamp deduplication; event supersession; future-session outcomes and overlap; concurrent oversell protection; and additive database preservation.

An additional Node VM smoke check executed the actual analysis-rendering function against a synthetic local API response, including all three scenarios, cyclical mismatch warning and HTML escaping. The temporary API server used synthetic records and mocked external providers. Browser automation could not initialize (`iab` unavailable; Chrome automation's app-server executable missing), so **visual/browser interaction QA remains unverified**. No claim of Windows runtime validation or improved investment returns is made.

## File map

- Ledger/persistence: `portfolio.py`, `db.py`, `storage.py`.
- Financial/import/model: new `financial_math.py`; `fundamentals.py`, `workbook.py`, `screener_fetch.py`, `sources.py`, `verify.py`, `scoring.py`, `valuation.py`.
- Retrieval and events: `autofill.py`, `prices.py`, `net.py`, `xlsx.py`, `news.py`, `events.py`, `tracker.py`, `monitor.py`, `daily.py`.
- Evidence/product: `recommendations.py`, `news_alerts.py`, `research.py`, `quality.py`, `risk.py`, `server.py`, `web/app.js`.
- New tests: `test_upgrade_ledger.py`, `test_upgrade_refresh.py`, `test_upgrade_finance.py`, `test_upgrade_news_research.py`, `test_upgrade_boundaries.py`. Existing regression fixtures/assertions updated in `test_news_alerts.py`, `test_monitor_telegram.py`, `test_notify_refresh.py`, `test_real_exports.py`, `test_server.py`.
- Reference decisions: [all 32 Argon files](ARGON_AUDIT.md).

Module paths above are under `india_screener/`; tests are under `tests/`. Implementation commits:

| Commit | Scope |
|---|---|
| `a7d8e85` | Atomic ledger, replay validation and fractional settlements |
| `6af46ad` | Financial history, inputs, imports and provider integrity |
| `e9f07eb` | Versioned cyclical/scoring comparisons and valuation/research evidence |
| `eeff9f1` | News disambiguation and event stabilization |
| `850c85d` | Dashboard/API integration, risk and boundary/migration coverage |

Documentation is committed separately. The README links these reports. Use `git log --oneline 7440f87..HEAD` and `git diff --stat 7440f87..HEAD` for the exact commit/file inventory.

## Deliberately deferred and remaining limitations

No historical strategy backtest was fabricated: point-in-time financial statements, historical universes and delisted returns are not present. Saved observations remain prospective. Cross-stock outcomes are correlated even after within-stock overlap removal; no confidence/alpha claim is justified. Provider revisions can still alter subsequently fetched adjusted returns; the benchmark's dividend convention may differ from stock adjusted closes. The archive processes all observations and can become expensive; pagination/aggregation needs scaling work.

No automatic FCFF/FCFE classification, maintenance-capex estimate, cycle-adjusted earnings forecast, sector-calibrated threshold, or financing projection is invented. Legacy CFO-based DCF remains approximate and can mix cash-flow/discount conventions. Scenario labels describe assumption sensitivity, not calibrated economic forecasts. Current-share inference can still require source verification; retaining older statements does not validate restatements or corporate-action adjustments.

Portfolio history is limited to unchanged quantities after the latest ledger transaction. It is not a full cash-aware, time-weighted or money-weighted account return. Acquisitions/disposals, demergers, complex corporate actions and tax-lot reporting are not automated.

News materiality remains explainable triage, not an extractor that proves financial impact or causation. The existing materiality module is retained. Already-notified event mappings are not rewritten to invent earlier alerts. NSE cookie warmup/provider session changes, Windows launch/toast refinements, shortlist metadata cleanup and notification report-name filtering are deferred. Older cached prices are not retroactively reparsed until refresh. Repeated delivery failures are visible after five attempts; retries continue rather than being falsely described as “exhausted.”

## Next three priorities

1. Review cyclical industry membership, obtain longer verified earnings histories, reconcile share adjustments, and compare complete-cycle assumptions before approving a production policy change.
2. Accumulate confirmed prospective recommendations with a consistent total-return benchmark and archived price vintages; evaluate economic outcomes and uncertainty before calibrating thresholds.
3. Complete visual QA and Windows validation, then rehearse deployment/rollback on a database copy; add scalable research aggregation and fuller cash-aware portfolio history as evidence permits.
