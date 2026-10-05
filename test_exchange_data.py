"""Tests for exchange_data using synthetic files in the exact NSE / BSE layouts.
Run: python test_exchange_data.py
"""
import tempfile
import types
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import exchange_data as ex

NSE_HEADER = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
              "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, "
              "NO_OF_TRADES, DELIV_QTY, DELIV_PER\n")
BSE_HEADER = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,"
              "FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,"
              "TtlTradgVol\n")

_NO_SLEEP = types.SimpleNamespace(sleep=lambda *_: None)   # affects exchange_data only
TODAY = date(2026, 10, 5)
HOLIDAY = date(2026, 9, 25)         # weekday with no file (404)
SPLIT_DAY = date(2026, 9, 16)       # 2-for-1 split ex-date for AAA


def weekdays(n_back):
    return [TODAY - timedelta(k) for k in range(n_back, -1, -1)
            if (TODAY - timedelta(k)).weekday() < 5 and (TODAY - timedelta(k)) != HOLIDAY]


def price_path(n_back):
    """Unadjusted close for AAA over trading days, with a 2:1 split on SPLIT_DAY."""
    days = weekdays(n_back)
    rng = np.random.default_rng(1)
    true = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, len(days)))  # split-adjusted truth
    raw = {d: (p / 2 if d >= SPLIT_DAY else p) for d, p in zip(days, true)}
    return days, raw


def make_http(n_back):
    days, raw = price_path(n_back)
    idx = {d: i for i, d in enumerate(days)}

    def fake(url, headers, retries=3, timeout=30):
        if "nsearchives" in url and "sec_bhavdata_full" in url:
            ds = url.split("_")[-1][:8]
            d = date(int(ds[4:]), int(ds[2:4]), int(ds[:2]))
            if d not in raw:
                return None
            prev = days[idx[d] - 1] if idx[d] > 0 else None
            pc = raw[prev] if prev else raw[d]
            if d == SPLIT_DAY:
                pc = raw[prev] / 2          # exchange publishes the adjusted prev close
            c = raw[d]
            txt = NSE_HEADER
            txt += (f"AAA, EQ, {d:%d-%b-%Y}, {pc:.2f}, {c:.2f}, {c*1.01:.2f}, {c*0.99:.2f}, "
                    f"{c:.2f}, {c:.2f}, {c:.2f}, 1000000, 100.0, 5000, 400000, 40.0\n")
            txt += (f"BBB, EQ, {d:%d-%b-%Y}, 50.00, 50.00, 51.00, 49.00, 50.00, 50.00, 50.00, "
                    f"2000, 10.0, 50, -, -\n")
            txt += (f"CCC, N1, {d:%d-%b-%Y}, 5.00, 5.00, 5.10, 4.90, 5.00, 5.00, 5.00, 10, 1, 1, -, -\n")
            return txt.encode()
        if "bseindia" in url:
            ds = url.split("_")[-3]
            d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:]))
            if d not in raw:
                return None
            return (BSE_HEADER +
                    f"{d},{d},CM,BSE,STK,500001,INE000A01011,AAA,A,AAA LTD,10,11,9,10,10,10,500\n"
                    f"{d},{d},CM,BSE,STK,500002,INE000B01011,BBB2,A,BBB TWO LTD,20,21,19,20,20,20,900\n"
                    ).encode()
        raise AssertionError(url)
    return fake


def with_tmp_cache():
    tmp = Path(tempfile.mkdtemp())
    ex.CACHE = {"NSE": tmp / "n", "BSE": tmp / "b"}
    return tmp


def test_parse_nse_quirks():
    raw = (NSE_HEADER +
           "AAA, EQ, 01-Oct-2026, 100.00, 101.00, 102.00, 99.00, 100.5, 100.50, 100.2, 12345, 12.3, 456, -, -\n"
           "AAA, BE, 01-Oct-2026, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -, -\n"
           "ZZZ, N1, 01-Oct-2026, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -, -\n").encode()
    df = ex.parse_nse(raw, date(2026, 10, 1))
    assert list(df["Key"]) == ["NSE:AAA"], df          # EQ preferred, non-equity dropped
    r = df.iloc[0]
    assert (r.Open, r.High, r.Low, r.Close, r.PrevClose, r.Volume) == (101.0, 102.0, 99.0, 100.5, 100.0, 12345.0)
    print("parse_nse OK")


def test_parse_nse_rejects_html():
    try:
        ex.parse_nse(b"<html><body>Access Denied</body></html>", date(2026, 10, 1))
    except Exception:
        print("parse_nse rejects HTML OK")
        return
    raise AssertionError("HTML error page was accepted")


def test_pipeline_split_and_holiday():
    with_tmp_cache()
    fake = make_http(560)
    ex._http_get = fake
    ex.time = _NO_SLEEP
    daily, failed = ex.load_days("NSE", 560, today=TODAY)
    assert failed == [], failed
    days, raw = price_path(560)
    assert HOLIDAY not in set(daily["Date"].dt.date)           # holiday simply absent
    px = ex.build_prices(daily, {"NSE:AAA", "NSE:BBB"}, failed)
    a = px["NSE:AAA"]
    assert a["Close"].pct_change().abs().max() < 0.08, "split must not appear as a -50% day"
    assert abs(a["Close"].iloc[-1] - raw[days[-1]]) < 0.01      # latest bar unchanged
    assert abs(px["NSE:BBB"]["Close"].iloc[0] - 50) < 1e-9      # no action => untouched
    # volume before split is doubled so volume stays comparable
    assert a["Volume"].iloc[0] > 1_500_000
    print("pipeline split/holiday OK")

    # second run must use the cache only (no network)
    ex._http_get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used"))
    ex.load_days("NSE", 560, today=TODAY)
    print("cache reuse OK")


def test_failed_day_not_used_for_adjustment():
    with_tmp_cache()
    ex._http_get = make_http(560)
    ex.time = _NO_SLEEP
    daily, _ = ex.load_days("NSE", 560, today=TODAY)
    # Simulate a missing day (download failed) and make sure no bogus factor appears
    gap = date(2026, 8, 12)
    daily = daily[daily["Date"] != pd.Timestamp(gap)]
    px = ex.build_prices(daily, {"NSE:BBB"}, [gap])
    assert abs(px["NSE:BBB"]["Close"].iloc[0] - 50) < 1e-9
    print("failed-day safety OK")


def test_blocked_site_raises():
    with_tmp_cache()
    def boom(*a, **k):
        raise ex.FetchError("HTTP 403")
    ex._http_get = boom
    try:
        ex.load_days("NSE", 60, today=TODAY)
    except ex.FetchError as e:
        assert "Could not download NSE" in str(e)
        print("blocked site error OK")
        return
    raise AssertionError("should have raised")


def _patch_lists_and_clock():
    tmp = with_tmp_cache()
    ex.NSE_CACHE_LIST = tmp / "nse.csv"
    ex.BSE_FILE = tmp / "bse.csv"
    ex.NSE_CACHE_LIST.write_text(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "AAA LTD,X,AAA,EQ,INE000A01011\nBBB LTD,X,BBB,EQ,INE000B01099\n")
    ex.BSE_FILE.write_text(
        "Scrip Code,COMPANY,ISIN No.,Close Price\n"
        "500001,AAA LTD,INE000A01011,10\n"
        "500002,BBB TWO LTD,INE000B01011,20\n"
        "500003,GHOST LTD,INE000C01011,30\n")
    ex._http_get = make_http(560)
    ex.time = _NO_SLEEP
    import datetime

    class D(datetime.date):
        @classmethod
        def today(cls):
            return TODAY
    ex.date = D


def test_modes():
    from scan import run_scan
    from screener import Params
    _patch_lists_and_clock()

    uni, px, info = ex.prepare("NSE")
    assert list(uni["key"]) == ["NSE:AAA", "NSE:BBB"] and set(px) == {"NSE:AAA", "NSE:BBB"}
    assert set(uni["index"]) == {"NSE 200"}

    uni, px, info = ex.prepare("BSE")
    assert len(uni) == 3 and set(uni["index"]) == {"BSE 200"}
    assert set(px) == {"BSE:500001", "BSE:500002"}                 # BSE prices only
    assert uni.set_index("key").loc["BSE:500001", "symbol"] == "AAA"   # ticker from BSE file
    assert any("500003" in w for w in info["warnings"]), info["warnings"]   # missing stock reported

    uni, px, info = ex.prepare("BOTH")
    idx = uni.set_index("symbol")["index"].to_dict()
    assert idx == {"AAA": "NSE 200 + BSE 200", "BBB": "NSE 200 only",
                   "BBB2": "BSE 200 only", "500003": "BSE 200 only"}, idx
    assert set(px) == {"NSE:AAA", "NSE:BBB", "BSE:500002"}          # NSE data for dual-listed
    print("modes NSE / BSE / BOTH OK")

    # BSE site blocked in BOTH mode: NSE results still returned, with a warning
    real = ex._http_get
    ex._http_get = lambda url, *a, **k: (_ for _ in ()).throw(ex.FetchError("HTTP 403")) \
        if "bseindia" in url else real(url, *a, **k)
    for d in ex.CACHE["BSE"].glob("*"):
        d.unlink()
    uni, px, info = ex.prepare("BOTH")
    assert set(uni["key"]) == {"NSE:AAA", "NSE:BBB"} and any("BSE data could not" in w for w in info["warnings"])
    try:
        ex.prepare("BSE")
    except ex.FetchError:
        print("BSE blocked: BOTH degrades gracefully, BSE mode errors OK")
        return
    raise AssertionError("BSE mode should raise when BSE is unreachable")


def test_real_lists_overlap():
    """Uses the bundled data/nse200.csv and data/bse200.csv (the files supplied by the client)."""
    real = Path(__file__).parent / "data"
    ex.NSE_CACHE_LIST, ex.BSE_FILE = real / "nse200.csv", real / "bse200.csv"
    nse, bse = ex.load_nse200(False), ex.load_bse200_file()
    assert len(nse) == 200 and len(bse) == 200
    cmp = ex.compare_indices(nse, bse)
    assert (len(cmp["both"]), len(cmp["bse_only"]), len(cmp["nse_only"])) == (175, 25, 25)
    assert "500488" in set(cmp["bse_only"]["code"])               # Abbott India: BSE 200 only
    print("real lists: 175 common / 25 BSE-only / 25 NSE-only OK")


if __name__ == "__main__":
    test_parse_nse_quirks()
    test_parse_nse_rejects_html()
    test_pipeline_split_and_holiday()
    test_failed_day_not_used_for_adjustment()
    test_blocked_site_raises()
    test_modes()
    test_real_lists_overlap()
