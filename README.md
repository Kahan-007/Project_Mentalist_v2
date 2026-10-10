# India Screener — data, news/event tracker and portfolio ledger

Python 3.10+, standard library only. No paid API, no broker API. Runs locally on Windows.

## What's here

| Module | Does |
|---|---|
| `prices.py` | Daily OHLCV from Yahoo's public chart endpoint (NSE `.NS`, BSE `.BO`, Nifty `^NSEI`), validated, cached per day, today's candle excluded until 16:00 IST |
| `universe.py` | Official NSE EQ list (EQUITY_L.csv) and clean short names for news search |
| `news.py` | Google News RSS per company: relevance-filtered, de-duplicated, tagged (merger, deal, legal/regulatory, results, governance, pledge, rating) |
| `events.py` | Market-model event study: confirms a news-session drop is abnormal (beyond Nifty and the stock's own noise); decides when price has levelled out |
| `tracker.py` | Stores headlines, groups them into one event per trading session, runs the event lifecycle, prevents duplicate alerts |
| `portfolio.py` | Exact Decimal FIFO ledger: buys, sells, fees, dividends, splits, bonuses; Zerodha tradebook CSV import (file, not API) |
| `daily.py` | The daily job: shortlist + holdings → prices, news, events, alerts, markdown report in `data/reports/` |
| `notify.py` | Alerts to `data/logs/alerts.log`, Windows toast, optional Telegram |
| `schedule.py` | Windows Task Scheduler entry (weekdays 18:30, runs late if the PC was off) |
| `fundamentals.py` | Reads Screener.in exports (.xlsx) or the CSV template, derives ROE, ROCE, FCF, CAGR etc. |
| `scoring.py` | Buffett / Munger / Lynch percentage-match scores, separate criteria for banks/NBFCs |
| `server.py`, `web/` | The local dashboard |
| `index250.py` | Nifty LargeMidcap 250 list: universe, business-type suggestions, industry, file-to-symbol matching |
| `verify.py` | Cross-checks: balance sheet, cash flow, quarters vs year, market cap, Screener vs Yahoo prices, index vs NSE list |
| `shortlist.py` | **The hook for the Buffett/Munger/Lynch scoring core** — it writes `data/shortlist.json` |

## Everyday use (Windows)

Double-click **Start Screener.bat**. The dashboard opens in your browser at http://127.0.0.1:8765 and only this computer can reach it.

- **My portfolio**: pick a stock, enter date, quantity, price and charges; or import a Zerodha tradebook CSV.
- **Top 250**: all 250 companies, always listed. Bulk-import Screener exports; filter by bucket (quality core, growth, two or more, all three), single philosophy, minimum scores, industry, Lynch category and size; sort any column; export what you see to CSV; shortlist everything shown in one click.
- **Current-price refresh**: upload a simple `Ticker,Latest_Price` CSV on Top 250 to bring today's price, market cap, P/E and margin-of-safety up to date between full Yahoo refreshes. It updates today's snapshot only, not the year-by-year history.
- **Combined workbook**: import `NSE_Top250_Financials_FY2017_FY2026.xlsm` (all 250 companies at once) on the Top 250 tab or with `py -m india_screener import-workbook FILE`, then click *Fill year-end prices and market caps from Yahoo* (or `py -m india_screener fill-prices`).
- **Analyse a stock**: prices, news and events for any NSE stock; upload its Screener.in "Export to Excel" file to get the Buffett, Munger and Lynch scores with every criterion shown.
- **Bulk ownership entry**: on Top 250, filter to the stocks you care about (e.g. Quality core, Two or more) and click *Fill in promoter / pledge / institutional % for shown stocks* — one table, one Screener link per row, one Save.
- **Scores & shortlist**: every imported company ranked; one click shortlists all stocks matching all three.
- **News alerts**: run the daily check on demand and read alerts and reports.
- **Settings**: G-Sec yield, pass mark, market-cap cut-offs, alerts.

Double-click **Set up daily check.bat** once to run the news/event check every weekday at 18:30.

## Command line (optional)

```
cd C:\path\to\india_screener_project
py -m unittest discover -s tests -p "test_*.py"
py -m india_screener check INFY
py -m india_screener shortlist add INFY HDFCBANK
py -m india_screener portfolio import-zerodha --file tradebook.csv
py -m india_screener daily --no-notify
py -m india_screener notify-test
py -m india_screener schedule install --time 18:30
```

Copy `config.example.json` to `config.json` to change thresholds or add Telegram details.

## Honest limits
- Yahoo's endpoint is unofficial; it can change or throttle. Everything goes through `prices.history()` so it can be swapped.
- A confirmed dip means the stock fell abnormally in the session the news became public. It does not prove the headline caused it.
- "Levelled out" is a stability test (calm range, no abnormal drift, trough held), not a forecast of recovery.
- Portfolio values use the last completed close. Record splits/bonuses in the ledger or values will be wrong.
- Scores are only as good as the imported statements. Use consolidated figures where they exist; the importer cannot tell.
- Capex is estimated from the balance sheet (change in net block + CWIP + depreciation) because the export has no capex line.
- Market-cap bands use cut-offs you set from AMFI's list, not a live ranking.

## Scoring rules in one paragraph
Each philosophy is a weighted percentage match with partial credit. Some criteria are **must-pass** (Buffett: consistent ROE, low debt,
margin of safety; Munger: high and durable ROCE/ROE; Lynch: not overpaying for growth, sound balance sheet) - a stock can only "match"
if it clears them. Munger's red flags (pledging, dilution, weak cash conversion, receivables, other income, low tax) only subtract.
Cyclicals are valued on mid-cycle earnings. Fewer than 6 years of data makes a result provisional.
