import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from exchange_data import prepare
from scan import run_scan
from screener import CHECK_LABELS, Params, dema, rsi

st.set_page_config(page_title="Breakout Screener – NSE/BSE 200", layout="wide")
st.title("📈 Breakout Screener — NSE 200 / BSE 200")
st.caption(
    "Fresh up-move after a downtrend. **Signal** = all 5 checks pass · "
    "**Watchlist** = 3–4 pass (setups still forming). Daily end-of-day data "
    "from the NSE and BSE websites."
)

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Universe")
    use_nse = st.checkbox("NSE 200", True)
    use_bse = st.checkbox("BSE 200", True)
    bse_extra = st.text_area(
        "BSE 200 symbols (optional)",
        help="Paste BSE scrip codes (e.g. 500325) or BSE tickers, comma / newline "
             "separated. Added to whatever is in data/bse200.csv.", height=80)
    refresh_nse = st.checkbox("Refresh NSE 200 list from NSE", False)

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
    go_btn = st.button("🔍 Run scan", type="primary", use_container_width=True)

params = Params(rsi_low=rsi_low, rsi_high=rsi_high, vol_avg_days=int(vol_days),
                pivot_window=pivot, trend_lookback=lookback,
                breakout_within=within, retest_zone=zone, line_tolerance=tol)

# ---------------------------------------------------------------- scan
if go_btn:
    if not (use_nse or use_bse):
        st.warning("Tick NSE 200 and/or BSE 200 in the sidebar.")
        st.stop()
    bar = st.progress(0.0, text="Starting…")
    try:
        uni, prices, info = prepare(
            use_nse, use_bse, bse_extra, refresh_nse,
            progress=lambda f, t: bar.progress(min(max(f, 0.0), 1.0), text=t))
    except Exception as e:  # noqa: BLE001
        bar.empty()
        st.error(str(e))
        st.stop()
    bar.progress(1.0, text="Scanning…")
    summary, store, skipped = run_scan(uni, params, prices)
    bar.empty()
    st.session_state.update(summary=summary, store=store, skipped=skipped, info=info,
                            scanned_at=pd.Timestamp.now(), n_uni=len(uni))

if "summary" not in st.session_state:
    st.info("Choose your universe in the sidebar and press **Run scan**. "
            "The very first run downloads about 400 daily files from NSE (and BSE) "
            "and can take several minutes; after that only new days are downloaded.")
    st.stop()

summary = st.session_state["summary"]
store = st.session_state["store"]
info = st.session_state["info"]

for w in info["warnings"]:
    st.warning(w)

if summary.empty:
    st.error(
        f"No stocks could be scanned (universe had {st.session_state['n_uni']} stocks, "
        f"{len(st.session_state['skipped'])} skipped for missing/short price history). "
        "Make sure NSE 200 is ticked and you are online, then press Run scan again.")
    st.stop()

last = info["last_date"]
st.caption(
    f"Data as of **{last:%d %b %Y}** (latest trading day available) · "
    f"scanned {len(summary)} of {st.session_state['n_uni']} stocks"
    + (f" · {len(st.session_state['skipped'])} skipped (short history)"
       if st.session_state["skipped"] else ""))


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
            st.dataframe(fmt(sub), use_container_width=True, hide_index=True)
            st.download_button("Download CSV", sub.to_csv(index=False),
                               f"screener_{score}of5.csv", key=f"dl{score}")
with tabs[3]:
    st.dataframe(fmt(summary), use_container_width=True, hide_index=True)
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
st.plotly_chart(fig, use_container_width=True)

st.write("**Checks:** " + " · ".join(
    f"{'✅' if res.checks[k] else '❌'} {v}" for k, v in CHECK_LABELS.items()))
