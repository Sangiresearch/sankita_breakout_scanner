# Breakout Screener — NSE 200 / BSE 200 (exchange data only)

Scans NSE 200 and BSE 200 stocks on your 5 checks and sorts them into
**Signal (5/5)**, **Watchlist 4/5** and **Watchlist 3/5**.
All prices and volume come from the NSE and BSE websites. No Yahoo or other third party.

## Run it (Windows)

1. Extract the zip, open the extracted folder, click the address bar, type `cmd`, press Enter.
2. Install (one time):
   `python -m pip install -r requirements.txt`  (or `pip install -r requirements.txt`)
3. Start the app:
   `python -m streamlit run app.py`  (or `py -m streamlit run app.py`)
4. Press **Run scan**. Keep the black window open while using the app.

Command line instead: `python scan.py` (writes `scan_results.csv`).
Logic tests: `python test_screener.py` and `python test_exchange_data.py`.

## Where the data comes from

| Item | Source |
|------|--------|
| NSE prices + volume | NSE daily full bhavcopy (`nsearchives.nseindia.com`) |
| BSE prices + volume | BSE daily equity bhavcopy (`bseindia.com/download/BhavCopy/Equity`) |
| NSE 200 list | NSE official `ind_nifty200list.csv` (downloaded automatically) |
| BSE 200 list | **You supply it** in `data/bse200.csv` (BSE has no stable download link) |

Each exchange file contains one trading day for all stocks. The first run downloads
about 400 files per exchange (several minutes). They are cached in `data/cache_nse`
and `data/cache_bse`, and later runs only fetch new days.

**Timing:** the exchanges publish the day's file after the close (NSE usually in the
evening). Before that, the latest available day is the previous trading day. The app
shows "Data as of ..." at the top.

**BSE list:** put BSE scrip codes (e.g. `500325`) or BSE tickers, one per row under the
`symbol` heading, in `data/bse200.csv`, or paste them in the sidebar. Stocks listed on
both exchanges are matched by ISIN and scanned once (on NSE).

## Corporate actions (splits / bonus)

Bhavcopy prices are not adjusted for splits or bonus issues. The app corrects for this
using the previous close that the exchange publishes in each file on the ex-date.
Days that failed to download are never used for this correction. The app warns you if
any days could not be downloaded; just run the scan again to retry them.

## The 5 checks

| # | Check | Definition |
|---|-------|-----------|
| 1 | RSI | Wilder RSI(14) between 50 and 65 on the latest close |
| 2 | DEMA | Close above DEMA 20, 50, 100 and 200 (DEMA = 2·EMA − EMA of EMA) |
| 3 | Volume | Latest volume above the average of the previous 10 sessions |
| 4 | Breakout | A descending line through two swing highs, which price has closed above within the last 20 bars and is above now |
| 5 | Retest | After the breakout, price first moves >2% above the line, then returns within 2% of it, closes at/above it, and today's close is still above the line |

Trendline and retest settings are adjustable in the sidebar. Trendline detection is
rule-based, so confirm each setup on the chart.

This is a screening tool, not investment advice.
