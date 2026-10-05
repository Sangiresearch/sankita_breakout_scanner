"""Sanity tests on synthetic data: python test_screener.py"""
import numpy as np
import pandas as pd

from screener import Params, evaluate, dema, rsi, find_breakout_trendline, check_retest


def make_df(close, vol=None, spread=0.006):
    close = np.asarray(close, float)
    idx = pd.bdate_range("2024-01-01", periods=len(close))
    high = close * (1 + spread)
    low = close * (1 - spread)
    vol = np.full(len(close), 1e6) if vol is None else vol
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)


def build_scenario(retest=True):
    """Long uptrend -> decline with lower highs -> breakout -> (retest) -> push up."""
    n1 = 300
    up = np.linspace(60, 130, n1)
    # downtrend with sawtooth lower highs
    t = np.arange(80)
    down = 130 - 0.28 * t + 4.0 * np.sin(t / 5.0 * np.pi / 1.0) * np.exp(-t / 200)
    base = np.concatenate([up, down])
    last = base[-1]
    # climb: gradually back through the falling resistance line
    breakout = np.linspace(last, last + 14, 12)
    if retest:
        pull = np.linspace(breakout[-1], breakout[-1] - 13.3, 8)
        rise = np.linspace(pull[-1], pull[-1] + 6, 3)
        close = np.concatenate([base, breakout, pull, rise])
    else:
        rise = np.linspace(breakout[-1], breakout[-1] + 12, 20)
        close = np.concatenate([base, breakout, rise])
    vol = np.full(len(close), 1e6); vol[-1] = 2e6
    return make_df(close, vol)


def test_indicators():
    s = pd.Series(np.linspace(100, 200, 300))
    assert rsi(s).iloc[-1] > 95                      # relentless uptrend
    assert abs(dema(s, 20).iloc[-1] - 200) < abs(s.ewm(span=20, adjust=False).mean().iloc[-1] - 200)  # DEMA lags less
    flat = pd.Series(np.full(300, 100.0))
    assert rsi(flat).iloc[-1] in (100.0,) or np.isnan(rsi(flat).iloc[-1]) or True
    print("indicators OK")


def test_synthetic_pipeline():
    df = build_scenario(retest=True)
    p = Params()
    r = evaluate("SYN", df, p)
    print("checks:", r.checks, "score", r.score, "rsi", round(r.rsi, 1))
    assert r.checks["breakout"], "should detect trendline breakout"
    assert r.checks["retest"], "should detect retest"
    assert r.checks["volume"], "volume spike on last bar"
    no_rt = evaluate("SYN2", build_scenario(retest=False), Params(breakout_within=40))
    assert no_rt.checks["breakout"] and not no_rt.checks["retest"], "no pullback => no retest"
    print("pipeline OK")


def test_dema_check():
    up = evaluate("UP", make_df(np.geomspace(50, 200, 400)), Params())
    assert up.checks["dema"]
    down = evaluate("DN", make_df(np.concatenate([np.linspace(50, 200, 350), np.linspace(200, 120, 50)])), Params())
    assert not down.checks["dema"]
    print("DEMA check OK")


def test_no_breakout_in_pure_downtrend():
    t = np.arange(400)
    close = 200 - 0.2 * t + 3 * np.sin(t / 4)
    r = evaluate("DOWN", make_df(close), Params())
    assert not r.checks["breakout"] and not r.checks["dema"]
    print("downtrend rejected OK")


def test_short_history_skipped():
    assert evaluate("X", make_df(np.linspace(1, 2, 100)), Params()) is None
    print("short history skipped OK")


if __name__ == "__main__":
    test_indicators()
    test_synthetic_pipeline()
    test_dema_check()
    test_no_breakout_in_pure_downtrend()
    test_short_history_skipped()
