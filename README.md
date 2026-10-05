# Breakout Screener: NSE 200 / BSE 200 (exchange data only)

Scans NSE 200 or BSE 200 stocks on 5 checks and sorts them into
**Signal (5/5)**, **Watchlist 4/5** and **Watchlist 3/5**.
All prices and volume come from the NSE and BSE websites. No Yahoo or other third party.

## One app, three views

Pick the index in the sidebar:

| View | Stocks | Prices and volume from |
|------|--------|------------------------|
| NSE 200 | the 200 stocks in `data/nse200.csv` | NSE daily files |
| BSE 200 | the 200 stocks in `data/bse200.csv` | BSE daily files |
| NSE 200 + BSE 200 | both lists, each company once (matched by ISIN) | NSE for stocks on both lists, BSE for the 25 BSE-only stocks |

The "NSE 200 vs BSE 200" panel on the main page lists the stocks that are only in one index
(currently 175 in both, 25 BSE-only, 25 NSE-only), with CSV downloads.

### Links for your clients

After the app address add:

- `?index=nse` opens on NSE 200 (this is the default)
- `?index=bse` opens on BSE 200
- `?index=both` opens on both lists
- add `&lock=1` to hide the switch, so that client can only see that index

Examples: `https://your-app.streamlit.app/?index=bse&lock=1`

## Run it on your computer (Windows)

1. Extract the zip, open the inner folder that contains `app.py`.
2. Install (one time): `python -m pip install -r requirements.txt`
3. Double-click `run_app.bat` (or run `python -m streamlit run app.py`).
4. Pick the index, press **Run scan**. Keep the black window open while using the app.

Command line instead: `python scan.py --index nse|bse|both` (writes `scan_results.csv`).
Daily job: `python daily_job.py` (or double-click `daily_scan.bat`).
Tests: `python test_screener.py` and `python test_exchange_data.py`.

## Where the data comes from

| Item | Source |
|------|--------|
| NSE prices + volume | NSE daily full bhavcopy (`nsearchives.nseindia.com`) |
| BSE prices + volume | BSE daily equity bhavcopy (`bseindia.com/download/BhavCopy/Equity`) |
| NSE 200 list | `data/nse200.csv` (NSE's `ind_nifty200list.csv`). Tick "Refresh NSE 200 list" to re-download |
| BSE 200 list | `data/bse200.csv` (BSE's constituents file: Scrip Code, COMPANY, ISIN No.) |

Each exchange file holds one trading day for all stocks. The first run downloads about 400
files per exchange (several minutes). They are cached in `data/cache_nse` and `data/cache_bse`,
and later runs fetch only new days.

**Timing:** the exchanges publish the day's file after the close. Before that, the latest
available day is the previous trading day. The app shows "Data as of ..." at the top.

### When the indices change (twice a year)

Replace `data/nse200.csv` and `data/bse200.csv` with the new constituent files, keeping the
same column layout. If you deployed online, upload the new files to GitHub (the app updates itself).

## Daily automatic scan

The app shows the **latest daily scan** straight away when it opens (no waiting), with the
date of the exchange data. **Run live scan now** does a fresh scan with your own settings.

`daily_job.py` does the daily work: it downloads the newest NSE and BSE files, scans NSE 200,
BSE 200 and both, and saves the results in the `results` folder. Two ways to run it every day:

**A. Online, automatically (GitHub Actions, free, needs no computer on)**
The file `.github/workflows/daily_scan.yml` runs it every weekday at 8:00 PM IST, after the
exchanges publish the day's files, and saves the results back into your repository. The online
app then picks them up by itself.
1. Put the project on GitHub (including the `.github/workflows/daily_scan.yml` file).
2. On github.com open your repository, then **Settings, Actions, General**, scroll to
   **Workflow permissions**, choose **Read and write permissions** and Save.
3. Open the **Actions** tab, click **Daily scan** on the left, then **Run workflow** to test it now.
   The first run takes 10 to 20 minutes; later runs are faster because the files are kept.
4. When the run shows a green tick, a `results` folder appears in the repository.

Caution: the exchange sites sometimes refuse requests that come from cloud servers. If the run
fails with "HTTP 403", use option B.

**B. On your own computer (Windows), automatically**
1. Double-click `daily_scan.bat` once to check it works. It saves the results in `results`.
2. Open **Task Scheduler**, choose **Create Basic Task**, name it "Daily scan", trigger **Daily**
   at 8:00 PM, action **Start a program**, and pick `daily_scan.bat`. In the task's properties
   tick "Run whether user is logged on or not" if you want it to run with the screen locked.
3. The app on that computer shows the results. To show them to clients online, upload the
   `results` folder to GitHub after each run.

## Corporate actions (splits / bonus)

Bhavcopy prices are not adjusted for splits or bonus issues. The app corrects for this
using the previous close that the exchange publishes in each file on the ex-date. Days that
failed to download are never used for this correction. The app warns if any day could not be
downloaded; run the scan again to retry.

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

## Notes on BSE data

For stocks traded on both exchanges, BSE volumes are usually lower than NSE volumes. The
volume check compares a stock only with its own recent average on the same exchange, so it
still works, but it can be noisier in the BSE 200 view.

## Password and sharing

The password screen switches on when `APP_PASSWORD` is set (in `.streamlit/secrets.toml`
locally, or in the **Secrets** box on Streamlit Community Cloud). Leave it unset for an
open link. The online copy needs the exchange sites to accept requests from the cloud
server; if they refuse, run the scan on your own computer and share the CSV downloads.
Check the NSE/BSE data-usage terms if you pass their data to clients commercially.

This is a screening tool, not investment advice.
