"""
Market data straight from the exchanges' own websites (no Yahoo / third parties).

Prices and volume
-----------------
  NSE : daily "full bhavcopy"  nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
  BSE : daily equity bhavcopy  bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV

Each file holds one trading day for every stock, so the app downloads one file
per day, caches it in data/cache_nse and data/cache_bse, and afterwards only
fetches new days. The first run needs roughly 400 files per exchange.

Stock lists
-----------
  NSE 200 : official CSV from nsearchives.nseindia.com (includes ISIN)
  BSE 200 : your own data/bse200.csv (BSE scrip codes or BSE tickers). BSE does
            not publish a stable machine-readable download link.
  Stocks present in both lists (matched by ISIN) are scanned once.

Corporate actions
-----------------
Bhavcopy prices are not split/bonus adjusted. Each file does carry the previous
close as adjusted by the exchange on the ex-date, so the history is back-adjusted
from (previous-close / yesterday's-close) on every ex-date. Days that failed to
download are never used for this, to avoid false adjustments.
"""
from __future__ import annotations

import io
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DATA_DIR = Path(__file__).parent / "data"
CACHE = {"NSE": DATA_DIR / "cache_nse", "BSE": DATA_DIR / "cache_bse"}
NSE_CACHE_LIST = DATA_DIR / "nse200.csv"
BSE_FILE = DATA_DIR / "bse200.csv"

NSE200_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
]
URLS = {
    "NSE": "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv",
    "BSE": "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d:%Y%m%d}_F_0000.CSV",
}
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {
    "NSE": {"User-Agent": _UA, "Accept": "text/csv,*/*", "Referer": "https://www.nseindia.com/"},
    "BSE": {"User-Agent": _UA, "Accept": "text/csv,*/*", "Referer": "https://www.bseindia.com/"},
}

UNIVERSE_COLS = ["symbol", "name", "key", "ISIN", "index"]


class FetchError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _http_get(url: str, headers: dict, retries: int = 3, timeout: int = 30):
    """Returns bytes, or None if the file does not exist (HTTP 404 = holiday)."""
    last = "unknown error"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.content
            if r.status_code == 404:
                return None
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)[:120]
        time.sleep(1.5 * (attempt + 1) + random.random())
    raise FetchError(last)


# --------------------------------------------------------------------------
# Parsing one day's file
# --------------------------------------------------------------------------
def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.strip().str.replace(",", ""), errors="coerce")


def parse_nse(raw: bytes, d: date) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), skipinitialspace=True, dtype=str)
    df.columns = [c.strip().upper() for c in df.columns]
    need = ["SYMBOL", "SERIES", "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE",
            "LOW_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"unexpected NSE file layout, missing {missing}")
    df["SYMBOL"] = df["SYMBOL"].str.strip()
    df["SERIES"] = df["SERIES"].str.strip()
    df = df[df["SERIES"].isin(["EQ", "BE", "BZ"])]
    df = df.assign(_rank=(df["SERIES"] != "EQ").astype(int)).sort_values("_rank")
    df = df.drop_duplicates("SYMBOL")
    return pd.DataFrame({
        "Key": "NSE:" + df["SYMBOL"],
        "Date": pd.Timestamp(d),
        "Open": _num(df["OPEN_PRICE"]), "High": _num(df["HIGH_PRICE"]),
        "Low": _num(df["LOW_PRICE"]), "Close": _num(df["CLOSE_PRICE"]),
        "PrevClose": _num(df["PREV_CLOSE"]), "Volume": _num(df["TTL_TRD_QNTY"]),
        "Ticker": df["SYMBOL"], "ISIN": "", "Name": df["SYMBOL"],
    }).reset_index(drop=True)


def parse_bse(raw: bytes, d: date) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), skipinitialspace=True, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    need = ["FinInstrmId", "TckrSymb", "OpnPric", "HghPric", "LwPric",
            "ClsPric", "PrvsClsgPric", "TtlTradgVol"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"unexpected BSE file layout, missing {missing}")
    code = df["FinInstrmId"].str.strip()
    return pd.DataFrame({
        "Key": "BSE:" + code,
        "Date": pd.Timestamp(d),
        "Open": _num(df["OpnPric"]), "High": _num(df["HghPric"]),
        "Low": _num(df["LwPric"]), "Close": _num(df["ClsPric"]),
        "PrevClose": _num(df["PrvsClsgPric"]), "Volume": _num(df["TtlTradgVol"]),
        "Ticker": df["TckrSymb"].str.strip(),
        "ISIN": df["ISIN"].str.strip() if "ISIN" in df.columns else "",
        "Name": (df["FinInstrmNm"].str.strip() if "FinInstrmNm" in df.columns
                 else df["TckrSymb"].str.strip()),
    }).dropna(subset=["Close"]).reset_index(drop=True)


_PARSERS = {"NSE": parse_nse, "BSE": parse_bse}


# --------------------------------------------------------------------------
# Downloading a date range (cached day by day)
# --------------------------------------------------------------------------
def _paths(exch: str, d: date):
    base = CACHE[exch] / f"{d:%Y%m%d}"
    return base.with_suffix(".pkl"), base.with_suffix(".none")


def _is_cached(exch: str, d: date) -> bool:
    pkl, none = _paths(exch, d)
    return pkl.exists() or none.exists()


def _fetch_day(exch: str, d: date, today: date) -> None:
    if _is_cached(exch, d):
        return
    pkl, none = _paths(exch, d)
    raw = _http_get(URLS[exch].format(d=d), HEADERS[exch])
    if raw is None:
        # No file: market holiday. Don't remember it for the last few days,
        # since today's file only appears after the close.
        if (today - d).days > 3:
            none.touch()
        return
    _PARSERS[exch](raw, d).to_pickle(pkl)
    time.sleep(0.15)


def load_days(exch: str, days_back: int = 560, progress=None, workers: int = 4,
              today: date | None = None):
    """Returns (daily DataFrame for all stocks, sorted list of failed dates)."""
    today = today or date.today()
    CACHE[exch].mkdir(parents=True, exist_ok=True)
    days = [today - timedelta(n) for n in range(days_back, -1, -1)
            if (today - timedelta(n)).weekday() < 5]
    todo = [d for d in days if not _is_cached(exch, d)]
    failures: dict[date, str] = {}

    if todo:
        done = 0
        with ThreadPoolExecutor(workers) as pool:
            futs = {pool.submit(_fetch_day, exch, d, today): d for d in todo}
            for f in as_completed(futs):
                d = futs[f]
                try:
                    f.result()
                except Exception as e:  # noqa: BLE001
                    failures[d] = str(e)
                done += 1
                if progress:
                    progress(done / len(todo), f"{exch}: downloaded {done} of {len(todo)} days")
        if len(todo) >= 5 and len(failures) > 0.5 * len(todo):
            example = next(iter(failures.values()))
            raise FetchError(
                f"Could not download {exch} data from the exchange website "
                f"({len(failures)} of {len(todo)} days failed; example: {example}). "
                "Check your internet connection or whether the site is blocking requests, "
                "then try again. Days already downloaded are kept.")

    frames = []
    for d in days:
        pkl, _ = _paths(exch, d)
        if pkl.exists():
            frames.append(pd.read_pickle(pkl))
    if not frames:
        raise FetchError(f"No {exch} price files available.")
    return pd.concat(frames, ignore_index=True), sorted(failures)


# --------------------------------------------------------------------------
# Corporate-action adjustment + OHLCV per stock
# --------------------------------------------------------------------------
def _adjust(g: pd.DataFrame, prev_map: pd.Series, unsafe: set) -> pd.DataFrame:
    g = g.sort_values("Date").drop_duplicates("Date", keep="last").set_index("Date")
    prev_row_date = g.index.to_series().shift(1)
    contiguous = prev_row_date.eq(prev_map.reindex(g.index)) & ~g.index.isin(list(unsafe))
    prev_close_yday = g["Close"].shift(1)
    ratio = g["PrevClose"] / prev_close_yday
    ratio = ratio.where(contiguous & (prev_close_yday > 0))
    ok = ((ratio - 1).abs() > 0.002) & (ratio > 0.02) & (ratio < 50)
    f = ratio.where(ok).fillna(1.0)                     # price factor on ex-date
    fv = f.where((f - 1).abs() > 0.05, 1.0)             # volume only for splits/bonus

    def cum_after(x: pd.Series) -> pd.Series:           # product of factors of LATER bars
        return x.iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1.0)

    cp, cv = cum_after(f), cum_after(fv)
    out = pd.DataFrame({
        "Open": g["Open"] * cp, "High": g["High"] * cp,
        "Low": g["Low"] * cp, "Close": g["Close"] * cp,
        "Volume": g["Volume"] / cv,
    })
    return out.dropna(subset=["Close", "High", "Low"])


def build_prices(daily: pd.DataFrame, keys, failed_days=()) -> dict[str, pd.DataFrame]:
    """{key: OHLCV DataFrame (adjusted)} for the requested keys."""
    if daily.empty:
        return {}
    market = pd.Index(sorted(daily["Date"].unique()))
    prev_map = pd.Series(market[:-1], index=market[1:])
    failed = pd.to_datetime(list(failed_days)) if len(failed_days) else pd.DatetimeIndex([])
    unsafe = set()
    if len(failed):
        for p, t in zip(market[:-1], market[1:]):
            if ((failed > p) & (failed < t)).any():
                unsafe.add(t)
    sub = daily[daily["Key"].isin(set(keys))]
    return {k: _adjust(g, prev_map, unsafe) for k, g in sub.groupby("Key")}


# --------------------------------------------------------------------------
# Stock lists
# --------------------------------------------------------------------------
def load_nse200(refresh: bool = False) -> pd.DataFrame:
    """NSE 200 constituents (data/nse200.csv; refreshed from NSE on request)."""
    if refresh or not NSE_CACHE_LIST.exists():
        raw, last = None, "no response"
        for url in NSE200_URLS:
            try:
                raw = _http_get(url, HEADERS["NSE"])
            except FetchError as e:
                last = str(e)
                continue
            if raw:
                break
        if raw:
            DATA_DIR.mkdir(exist_ok=True)
            NSE_CACHE_LIST.write_bytes(raw)
        elif not NSE_CACHE_LIST.exists():
            raise FetchError(
                f"Could not download the NSE 200 list ({last}). Download it from "
                f"{NSE200_URLS[0]} and save it as {NSE_CACHE_LIST}.")
    df = pd.read_csv(NSE_CACHE_LIST, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    sym = df["Symbol"].str.strip()
    return pd.DataFrame({
        "symbol": sym,
        "name": df["Company Name"].str.strip(),
        "key": "NSE:" + sym,
        "ISIN": df["ISIN Code"].str.strip() if "ISIN Code" in df.columns else "",
        "index": "NSE 200",
    })[UNIVERSE_COLS]


def _pick(df: pd.DataFrame, *needles: str):
    for c in df.columns:
        if any(n in c.lower() for n in needles):
            return c
    return None


def load_bse200_file() -> pd.DataFrame:
    """BSE 200 constituents from data/bse200.csv (BSE's own constituents file).
    Needs a scrip-code column; company name and ISIN columns are used if present.
    Returns DataFrame[code, name, ISIN]."""
    if not BSE_FILE.exists():
        raise FetchError(f"BSE 200 list not found. Save BSE's constituents file as {BSE_FILE}.")
    df = pd.read_csv(BSE_FILE, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    code_c = _pick(df, "scrip", "security code")
    if code_c is None or df.empty:
        raise FetchError(
            f"{BSE_FILE.name} has no BSE 200 stocks. It should be BSE's constituents file "
            "with columns such as 'Scrip Code', 'COMPANY' and 'ISIN No.'.")
    name_c, isin_c = _pick(df, "company", "name"), _pick(df, "isin")
    out = pd.DataFrame({
        "code": df[code_c].str.strip(),
        "name": df[name_c].str.strip() if name_c else df[code_c].str.strip(),
        "ISIN": df[isin_c].str.strip() if isin_c else "",
    })
    return out.dropna(subset=["code"]).drop_duplicates("code").reset_index(drop=True)


def bse_universe(file_df: pd.DataFrame, bse_daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """Universe rows for BSE 200. Ticker and full name are taken from BSE's own
    daily file when available."""
    meta = None
    if bse_daily is not None and len(bse_daily):
        meta = (bse_daily.sort_values("Date").drop_duplicates("Key", keep="last")
                .set_index("Key"))
    rows = []
    for _, r in file_df.iterrows():
        key = f"BSE:{r['code']}"
        m = meta.loc[key] if meta is not None and key in meta.index else None
        tick = m["Ticker"] if m is not None and pd.notna(m["Ticker"]) and m["Ticker"] else r["code"]
        name = m["Name"] if m is not None and pd.notna(m["Name"]) and m["Name"] else r["name"]
        isin = r["ISIN"] if isinstance(r["ISIN"], str) and r["ISIN"] else (
            m["ISIN"] if m is not None and isinstance(m["ISIN"], str) else "")
        rows.append({"symbol": tick, "name": name, "key": key, "ISIN": isin, "index": "BSE 200"})
    return pd.DataFrame(rows, columns=UNIVERSE_COLS)


def merge_universe(nse: pd.DataFrame, bse: pd.DataFrame) -> pd.DataFrame:
    """'Both' mode. A company on both lists (matched by ISIN) is kept once, on NSE."""
    both = (set(nse["ISIN"]) & set(bse["ISIN"])) - {"", None}
    nse = nse.copy()
    nse["index"] = nse["ISIN"].map(lambda i: "NSE 200 + BSE 200" if i in both else "NSE 200 only")
    bse = bse[~bse["ISIN"].isin(both)].copy()
    bse["index"] = "BSE 200 only"
    return pd.concat([nse, bse], ignore_index=True).sort_values("symbol").reset_index(drop=True)


def compare_indices(nse_uni: pd.DataFrame, bse_file: pd.DataFrame) -> dict:
    """How the two lists differ, matched by ISIN."""
    n_isin, b_isin = set(nse_uni["ISIN"]), set(bse_file["ISIN"])
    return {
        "both": nse_uni[nse_uni["ISIN"].isin(b_isin)][["symbol", "name", "ISIN"]].reset_index(drop=True),
        "bse_only": bse_file[~bse_file["ISIN"].isin(n_isin)][["code", "name", "ISIN"]].reset_index(drop=True),
        "nse_only": nse_uni[~nse_uni["ISIN"].isin(b_isin)][["symbol", "name", "ISIN"]].reset_index(drop=True),
    }


# --------------------------------------------------------------------------
# One call for the app / CLI
# --------------------------------------------------------------------------
def prepare(mode: str = "NSE", refresh_nse: bool = False, days_back: int = 560, progress=None):
    """mode: 'NSE' (NSE 200, NSE data), 'BSE' (BSE 200, BSE data) or
    'BOTH' (union; NSE data for stocks on both lists, BSE data for BSE-only stocks).
    Returns (universe, {key: OHLCV}, info)."""
    mode = mode.upper()
    if mode not in ("NSE", "BSE", "BOTH"):
        raise ValueError("mode must be NSE, BSE or BOTH")
    info = {"warnings": [], "last_date": None, "failed_days": {}}
    prices: dict[str, pd.DataFrame] = {}
    nse_uni = bse_uni = None

    if mode in ("NSE", "BOTH"):
        nse_uni = load_nse200(refresh_nse)
        daily, failed = load_days("NSE", days_back, progress)
        info["last_date"] = daily["Date"].max()
        info["failed_days"]["NSE"] = failed
        prices.update(build_prices(daily, nse_uni["key"], failed))

    if mode in ("BSE", "BOTH"):
        bse_file = load_bse200_file()
        daily, failed = None, []
        try:
            daily, failed = load_days("BSE", days_back, progress)
        except FetchError as e:
            if mode == "BSE":
                raise
            info["warnings"].append(f"BSE data could not be downloaded, so the BSE-only stocks were not scanned. {e}")
        bse_uni = bse_universe(bse_file, daily)
        if daily is not None:
            info["failed_days"]["BSE"] = failed
            if info["last_date"] is None:
                info["last_date"] = daily["Date"].max()
            absent = bse_uni[~bse_uni["key"].isin(set(daily["Key"]))]
            if len(absent):
                info["warnings"].append(
                    f"{len(absent)} BSE 200 stock(s) are not in BSE's latest price files "
                    "(for example suspended or newly listed): " + ", ".join(absent["symbol"].head(8)))
            want = bse_uni["key"] if mode == "BSE" else \
                bse_uni[~bse_uni["ISIN"].isin(set(nse_uni["ISIN"]))]["key"]
            prices.update(build_prices(daily, want, failed))

    for exch, failed in info["failed_days"].items():
        if failed:
            info["warnings"].append(
                f"{exch}: {len(failed)} day(s) could not be downloaded and were left out "
                "(run the scan again to retry).")

    if mode == "NSE":
        uni = nse_uni
    elif mode == "BSE":
        uni = bse_uni
    elif prices and "BSE" in info["failed_days"]:
        uni = merge_universe(nse_uni, bse_uni)
    else:                                   # BSE download failed in 'both' mode
        uni = nse_uni
    return uni.reset_index(drop=True), prices, info
