import hmac
import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from exchange_data import compare_indices, load_bse200_file, load_nse200, prepare
from scan import run_scan
from screener import CHECK_LABELS, Params, dema, evaluate, rsi

st.set_page_config(page_title="Breakout Screener – NSE/BSE 200", layout="wide")


def _password_gate() -> None:
    """If APP_PASSWORD is set (Streamlit secrets or environment variable),
    nothing else is shown until the right password is entered."""
    try:
        expected = st.secrets.get("APP_PASSWORD")
    except Exception:  # no secrets file
        expected = None
    expected = expected or os.environ.get("APP_PASSWORD")
    if not expected or st.session_state.get("auth_ok"):
        return
    st.title("🔒 Breakout Screener")
    pw = st.text_input("Password", type="password")
    if pw:
        if hmac.compare_digest(pw.encode(), str(expected).encode()):
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()


_password_gate()

RESULTS_DIR = Path(os.environ.get("RESULTS_DIR", Path(__file__).parent / "results"))


@st.cache_data(show_spinner=False)
def load_saved(mode: str, stamp: float):
    """Latest daily results written by daily_job.py (None if not available)."""
    m = mode.lower()
    sp, cp, mp = (RESULTS_DIR / f"{m}_{k}" for k in ("summary.csv", "charts.csv", "meta.json"))
    if not (sp.exists() and cp.exists() and mp.exists()):
        return None
    summary = pd.read_csv(sp)
    charts = pd.read_csv(cp, parse_dates=["Date"])
    store = {}
    for key, g in charts.groupby("Key"):
        df = g.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]]
        res = evaluate(key, df, Params())
        if res is not None:
            store[key] = (df, res)
    meta = json.loads(mp.read_text())
    info = {"warnings": meta.get("warnings", []), "last_date": pd.Timestamp(meta["data_date"])}
    return {"summary": summary, "store": store, "info": info,
            "n_uni": meta["n_universe"], "skipped": [None] * meta["n_skipped"],
            "when": meta["generated_at"], "live": False}


def saved_for(mode: str):
    mp = RESULTS_DIR / f"{mode}_meta.json"
    return load_saved(mode, mp.stat().st_mtime) if mp.exists() else None

# ------------------------------------------------------------ index choice
# Share links:  ?index=nse   ?index=bse   ?index=both
# Add &lock=1 to hide the switch (a client link that cannot change index).
LABELS = {"nse": "NSE 200", "bse": "BSE 200", "both": "NSE 200 + BSE 200"}
qp = st.query_params
preset = str(qp.get("index", "nse")).lower()
preset = preset if preset in LABELS else "nse"
locked = str(qp.get("lock", "")).lower() in ("1", "true", "yes")

with st.sidebar:
    st.header("Index")
    if locked:
        mode = preset
        st.write(f"**{LABELS[mode]}**")
    else:
        keys = list(LABELS)
        mode = st.radio("Stocks to scan", keys, index=keys.index(preset),
                        format_func=LABELS.get)
        if mode != preset:
            st.query_params["index"] = mode
    refresh_nse = False if locked else st.checkbox("Refresh NSE 200 list from NSE", False)

    st.header("Parameters")
    c1, c2 = st.columns(2)
    rsi_low = c1.number_input("RSI min", 0.0, 100.0, 50.0)
    rsi_high = c2.number_input("RSI max", 0.0, 100.0, 65.0)
    vol_days = st.number_input("Volume avg days", 3, 50, 10)
    with st.expander("Trendline / retest settings"):
        pivot = st.slider("Swing-high window (bars each side)", 2, 10, 5)
        lookback = st.slider("Trendline lookback (bars)", 60, 250, 150)
        within = st.slider("Breakout must be within last N bars", 3, 60, 20)
        zone = st.slider("Retest zone above line (%)", 0.5, 5.0, 2.0, 0.5) / 100
        tol = st.slider("Line tolerance (%)", 0.0, 2.0, 0.5, 0.25) / 100
    go_btn = st.button("🔍 Run live scan now", type="primary", width="stretch")

params = Params(rsi_low=rsi_low, rsi_high=rsi_high, vol_avg_days=int(vol_days),
                pivot_window=pivot, trend_lookback=lookback,
                breakout_within=within, retest_zone=zone, line_tolerance=tol)

st.title(f"📈 Breakout Screener — {LABELS[mode]}")
SOURCE = {
    "nse": "NSE 200 stocks with prices and volume from the NSE website.",
    "bse": "BSE 200 stocks with prices and volume from the BSE website.",
    "both": "NSE 200 and BSE 200 together. Stocks on both lists use NSE data; "
            "BSE-only stocks use BSE data.",
}
st.caption(
    "Fresh up-move after a downtrend. **Signal** = all 5 checks pass · "
    "**Watchlist** = 3–4 pass (setups still forming). Daily end-of-day data. "
    + SOURCE[mode])

# ------------------------------------------------------------ list comparison
with st.expander("NSE 200 vs BSE 200: how the two lists differ"):
    try:
        cmp = compare_indices(load_nse200(False), load_bse200_file())
        m1, m2, m3 = st.columns(3)
        m1.metric("In both lists", len(cmp["both"]))
        m2.metric("BSE 200 only", len(cmp["bse_only"]))
        m3.metric("NSE 200 only", len(cmp["nse_only"]))
        t1, t2 = st.tabs(["BSE 200 only", "NSE 200 only"])
        with t1:
            st.dataframe(cmp["bse_only"].rename(columns={"code": "BSE scrip code"}),
                         width="stretch", hide_index=True)
            st.download_button("Download CSV", cmp["bse_only"].to_csv(index=False),
                               "bse200_only.csv", key="dl_bse_only")
        with t2:
            st.dataframe(cmp["nse_only"].rename(columns={"symbol": "NSE symbol"}),
                         width="stretch", hide_index=True)
            st.download_button("Download CSV", cmp["nse_only"].to_csv(index=False),
                               "nse200_only.csv", key="dl_nse_only")
    except Exception as e:  # noqa: BLE001
        st.info(f"Comparison not available: {e}")

# ---------------------------------------------------------------- results
# Either a live scan started from this page, or the latest daily scan saved
# by daily_job.py.
live = st.session_state.setdefault("live", {})
if go_btn:
    bar = st.progress(0.0, text="Starting…")
    try:
        uni, prices, info = prepare(
            mode.upper(), refresh_nse,
            progress=lambda f, t: bar.progress(min(max(f, 0.0), 1.0), text=t))
    except Exception as e:  # noqa: BLE001
        bar.empty()
        st.error(str(e))
        st.stop()
    bar.progress(1.0, text="Scanning…")
    summary, store, skipped = run_scan(uni, params, prices)
    bar.empty()
    live[mode] = {"summary": summary, "store": store, "info": info, "n_uni": len(uni),
                  "skipped": skipped, "live": True,
                  "when": pd.Timestamp.now().strftime("%Y-%m-%dT%H:%M")}

pack = live.get(mode) or saved_for(mode)
if pack is None:
    st.info("No results yet for this index. The daily scan has not run. "
            "Press **Run live scan now**. The first run downloads about 400 daily files "
            "from the exchange website and can take several minutes.")
    st.stop()

summary, store, info = pack["summary"], pack["store"], pack["info"]
when = pack["when"].replace("T", " ").split("+")[0]
if pack["live"]:
    st.caption(f"Live scan run on {when} with your settings.")
else:
    st.caption(f"Showing the latest daily scan (made {when} IST) with the default settings. "
               "Press **Run live scan now** to use your own settings.")

for w in info["warnings"]:
    st.warning(w)

if summary.empty:
    st.error(
        f"No stocks could be scanned (universe had {pack['n_uni']} stocks, "
        f"{len(pack['skipped'])} skipped for missing/short price history). "
        "Check you are online, then press Run live scan now again.")
    st.stop()

last = info["last_date"]
age = (pd.Timestamp.now().normalize() - last.normalize()).days
if age > 5:
    st.warning(f"This data is {age} days old. The daily scan may have stopped working.")
st.caption(
    f"Data as of **{last:%d %b %Y}** (latest trading day in the exchange files) · "
    f"scanned {len(summary)} of {pack['n_uni']} stocks"
    + (f" · {len(pack['skipped'])} skipped (short history)" if pack["skipped"] else ""))


# ---------------------------------------------------------------- tables
def fmt(df: pd.DataFrame) -> pd.DataFrame:
    d = df.drop(columns=["Ticker"]).copy()
    for lbl in CHECK_LABELS.values():
        d[lbl] = d[lbl].map({True: "✅", False: "❌"})
    return d


n5, n4, n3 = (int((summary["Score"] == s).sum()) for s in (5, 4, 3))
m1, m2, m3 = st.columns(3)
m1.metric("🟢 Signals (5/5)", n5)
m2.metric("🟡 Watchlist 4/5", n4)
m3.metric("🟠 Watchlist 3/5", n3)

tabs = st.tabs([f"🟢 Signals ({n5})", f"🟡 4 of 5 ({n4})", f"🟠 3 of 5 ({n3})", "All results"])
for tab, score in zip(tabs[:3], (5, 4, 3)):
    with tab:
        sub = summary[summary["Score"] == score]
        if sub.empty:
            st.write("No stocks match today.")
        else:
            st.dataframe(fmt(sub), width="stretch", hide_index=True)
            st.download_button("Download CSV", sub.to_csv(index=False),
                               f"screener_{score}of5.csv", key=f"dl{score}")
with tabs[3]:
    st.dataframe(fmt(summary), width="stretch", hide_index=True)
    st.download_button("Download all CSV", summary.to_csv(index=False), "screener_all.csv")

# ---------------------------------------------------------------- chart
st.divider()
st.subheader("Chart")
cands = summary[summary["Score"] >= 3]
if cands.empty:
    st.stop()
label = cands.apply(lambda r: f"{r['Symbol']} — {r['Score']}/5", axis=1).tolist()
pick = st.selectbox("Stock", label)
row = cands.iloc[label.index(pick)]
df, res = store[row["Ticker"]]
view = df.iloc[-180:]
n = len(df)

fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.2, 0.2],
                    vertical_spacing=0.03)
fig.add_trace(go.Candlestick(x=view.index, open=view.Open, high=view.High, low=view.Low,
                             close=view.Close, name="Price"), row=1, col=1)
for p_, colr in zip((20, 50, 100, 200), ("#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd")):
    fig.add_trace(go.Scatter(x=view.index, y=dema(df.Close, p_).loc[view.index],
                             name=f"DEMA {p_}", line=dict(width=1.2, color=colr)), row=1, col=1)
if res.trendline:
    tl = res.trendline
    xs = [df.index[tl.i1], df.index[-1]]
    ys = [tl.value_at(tl.i1), tl.value_at(n - 1)]
    fig.add_trace(go.Scatter(x=xs, y=ys, name="Descending trendline",
                             line=dict(color="red", dash="dash", width=2)), row=1, col=1)
    fig.add_vline(x=df.index[tl.breakout_idx], line_dash="dot", line_color="gray", row=1, col=1)
fig.add_trace(go.Scatter(x=view.index, y=rsi(df.Close).loc[view.index], name="RSI(14)",
                         line=dict(color="#555")), row=2, col=1)
fig.add_hrect(y0=50, y1=65, fillcolor="green", opacity=0.1, line_width=0, row=2, col=1)
fig.add_trace(go.Bar(x=view.index, y=view.Volume, name="Volume", marker_color="#999"), row=3, col=1)
fig.add_trace(go.Scatter(x=view.index, y=df.Volume.rolling(10).mean().loc[view.index],
                         name="Vol 10d avg", line=dict(color="orange")), row=3, col=1)
fig.update_layout(height=750, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
st.plotly_chart(fig, width="stretch")

st.write("**Checks:** " + " · ".join(
    f"{'✅' if res.checks[k] else '❌'} {v}" for k, v in CHECK_LABELS.items()))
