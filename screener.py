"""
Core screening engine: the 5 checks.

All functions take a daily OHLCV DataFrame with columns
Open, High, Low, Close, Volume (index = dates, ascending).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------
# Parameters (all adjustable from the app sidebar)
# --------------------------------------------------------------------------
@dataclass
class Params:
    rsi_period: int = 14
    rsi_low: float = 50.0
    rsi_high: float = 65.0
    dema_periods: tuple = (20, 50, 100, 200)
    vol_avg_days: int = 10
    # Trendline
    pivot_window: int = 5            # bars either side to confirm a swing high
    trend_lookback: int = 150        # bars searched for swing highs
    min_line_span: int = 10          # min bars between the two anchor highs
    breakout_within: int = 20        # breakout must have happened in last N bars
    line_tolerance: float = 0.005    # 0.5% slack when checking line validity
    # Retest
    retest_zone: float = 0.02        # low must come within 2% above the line
    retest_hold: float = 0.005       # and close must not be >0.5% below it


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100.0).where(avg_gain.notna())


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def dema(s: pd.Series, n: int) -> pd.Series:
    """Double EMA = 2*EMA - EMA(EMA)."""
    e1 = ema(s, n)
    e2 = ema(e1, n)
    return 2 * e1 - e2


# --------------------------------------------------------------------------
# Trendline breakout + retest
# --------------------------------------------------------------------------
@dataclass
class Trendline:
    i1: int            # positional index of first anchor high
    i2: int            # positional index of second anchor high
    p1: float
    p2: float
    slope: float       # price change per bar (negative)
    breakout_idx: int  # positional index of first close above the line

    def value_at(self, i: int) -> float:
        return self.p1 + self.slope * (i - self.i1)


def swing_highs(high: pd.Series, window: int) -> list[int]:
    """Positional indices of confirmed swing highs (pivot highs)."""
    h = high.values
    out = []
    for i in range(window, len(h) - window):
        seg = h[i - window: i + window + 1]
        if h[i] == seg.max() and (seg == h[i]).sum() == 1:
            out.append(i)
    return out


def find_breakout_trendline(df: pd.DataFrame, p: Params) -> Optional[Trendline]:
    """
    Find the most recent valid descending trendline (through two swing highs)
    that price has closed above within the last `breakout_within` bars.

    A line is valid when, from the first anchor up to the breakout bar,
    no close sits above it (beyond a small tolerance) and no bar's high
    pierces it by more than the tolerance between the two anchors.
    """
    n = len(df)
    if n < p.pivot_window * 2 + p.min_line_span + 5:
        return None

    start = max(0, n - p.trend_lookback)
    sub_high = df["High"].iloc[start:]
    piv = [start + i for i in swing_highs(sub_high, p.pivot_window)]
    if len(piv) < 2:
        return None

    close = df["Close"].values
    high = df["High"].values
    best: Optional[Trendline] = None

    for i1, i2 in combinations(piv, 2):
        if i2 - i1 < p.min_line_span:
            continue
        p1, p2 = high[i1], high[i2]
        if p2 >= p1:            # must be descending
            continue
        slope = (p2 - p1) / (i2 - i1)
        line = lambda i: p1 + slope * (i - i1)  # noqa: E731

        # No high between the anchors may pierce the line materially
        ok = True
        for k in range(i1 + 1, i2):
            if high[k] > line(k) * (1 + p.line_tolerance):
                ok = False
                break
        if not ok:
            continue

        # First close above the line after the second anchor
        b = None
        for k in range(i2 + 1, n):
            if close[k] > line(k):
                b = k
                break
        if b is None:
            continue

        # No close above the line between anchor 2 and breakout is true by
        # construction of b. Breakout must be recent.
        if n - 1 - b > p.breakout_within:
            continue

        cand = Trendline(i1, i2, float(p1), float(p2), float(slope), b)
        # Prefer the most recent breakout, then the longest span
        if (best is None
                or cand.breakout_idx > best.breakout_idx
                or (cand.breakout_idx == best.breakout_idx
                    and (cand.i2 - cand.i1) > (best.i2 - best.i1))):
            best = cand
    return best


def check_retest(df: pd.DataFrame, tl: Trendline, p: Params) -> bool:
    """
    After the breakout bar, price must have come back to the line
    (low within `retest_zone` above it, or dipping slightly below) while
    closing back at/above it, and today's close must still be above the line.
    """
    n = len(df)
    low = df["Low"].values
    close = df["Close"].values
    if tl.breakout_idx >= n - 1:
        return False  # breakout is today, no retest yet
    if close[-1] <= tl.value_at(n - 1):
        return False
    # Price must first move clearly away from the line (close beyond the
    # retest zone, counting the breakout bar itself); only a later return
    # to the line counts as a retest. This stops the bars immediately after
    # the breakout from being mistaken for a pullback.
    extended = close[tl.breakout_idx] > tl.value_at(tl.breakout_idx) * (1 + p.retest_zone)
    for k in range(tl.breakout_idx + 1, n):
        line_k = tl.value_at(k)
        if extended:
            touched = low[k] <= line_k * (1 + p.retest_zone)
            held = close[k] >= line_k * (1 - p.retest_hold)
            if touched and held:
                return True
        if close[k] > line_k * (1 + p.retest_zone):
            extended = True
    return False


# --------------------------------------------------------------------------
# Full evaluation of one stock
# --------------------------------------------------------------------------
@dataclass
class Result:
    symbol: str
    close: float = np.nan
    rsi: float = np.nan
    checks: dict = field(default_factory=dict)
    score: int = 0
    details: dict = field(default_factory=dict)
    trendline: Optional[Trendline] = None


CHECK_LABELS = {
    "rsi": "RSI 50–65",
    "dema": "Above DEMA 20/50/100/200",
    "volume": "Volume > 10d avg",
    "breakout": "Trendline breakout",
    "retest": "Successful retest",
}


def evaluate(symbol: str, df: pd.DataFrame, p: Params = Params()) -> Optional[Result]:
    df = df.dropna(subset=["Close", "High", "Low", "Volume"]).copy()
    # Need enough history to warm up the 200 DEMA
    if len(df) < max(p.dema_periods) + 50:
        return None

    close = df["Close"]
    r = Result(symbol=symbol, close=float(close.iloc[-1]))

    # 1. RSI
    rsi_val = float(rsi(close, p.rsi_period).iloc[-1])
    r.rsi = rsi_val
    r.checks["rsi"] = bool(p.rsi_low <= rsi_val <= p.rsi_high)

    # 2. Price above all DEMAs
    dema_vals = {n: float(dema(close, n).iloc[-1]) for n in p.dema_periods}
    r.details["dema"] = dema_vals
    r.checks["dema"] = all(r.close > v for v in dema_vals.values())

    # 3. Volume above its N-day average (average of the previous N sessions)
    prev_avg = float(df["Volume"].iloc[-(p.vol_avg_days + 1):-1].mean())
    vol = float(df["Volume"].iloc[-1])
    r.details["volume"] = vol
    r.details["vol_avg"] = prev_avg
    r.details["vol_ratio"] = vol / prev_avg if prev_avg else np.nan
    r.checks["volume"] = bool(prev_avg > 0 and vol > prev_avg)

    # 4. Descending trendline breakout  /  5. Retest
    tl = find_breakout_trendline(df, p)
    r.trendline = tl
    r.checks["breakout"] = tl is not None and r.close > tl.value_at(len(df) - 1)
    r.checks["retest"] = bool(tl is not None and check_retest(df, tl, p))
    if tl is not None:
        r.details["breakout_date"] = df.index[tl.breakout_idx]
        r.details["line_now"] = tl.value_at(len(df) - 1)

    r.score = int(sum(r.checks.values()))
    return r
