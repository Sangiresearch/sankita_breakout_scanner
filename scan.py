"""Run the screener over a universe. Usable from the app or the command line:

    python scan.py                  # NSE 200 (NSE prices)
    python scan.py --index bse      # BSE 200 (BSE prices)
    python scan.py --index both     # both lists together
    python scan.py --min-score 3    # CSV output threshold
"""
from __future__ import annotations

import argparse

import pandas as pd

from exchange_data import prepare
from screener import CHECK_LABELS, Params, evaluate


def run_scan(universe: pd.DataFrame, params: Params, prices: dict):
    """Returns (summary DataFrame, {key: (df, Result)}, skipped keys)."""
    rows, store, skipped = [], {}, []
    for _, u in universe.iterrows():
        df = prices.get(u["key"])
        res = evaluate(u["key"], df, params) if df is not None else None
        if res is None:
            skipped.append(u["symbol"])
            continue
        store[u["key"]] = (df, res)
        row = {
            "Symbol": u["symbol"], "Name": u["name"], "Index": u["index"],
            "Ticker": u["key"], "Close": round(res.close, 2),
            "RSI": round(res.rsi, 1),
            "Vol / 10d avg": round(res.details.get("vol_ratio", float("nan")), 2),
            "Breakout date": (res.details["breakout_date"].date()
                              if "breakout_date" in res.details else None),
            "Score": res.score,
        }
        for k, label in CHECK_LABELS.items():
            row[label] = res.checks[k]
        row["Failed"] = ", ".join(CHECK_LABELS[k] for k, v in res.checks.items() if not v)
        rows.append(row)

    summary = pd.DataFrame(rows)
    if len(summary):
        summary = summary.sort_values(["Score", "Symbol"], ascending=[False, True]).reset_index(drop=True)
    return summary, store, skipped


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", choices=["nse", "bse", "both"], default="nse")
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--out", default="scan_results.csv")
    a = ap.parse_args()

    uni, prices, info = prepare(a.index,
                                progress=lambda f, t: print(f"  {t}      ", end="\r"))
    print(f"\nUniverse: {len(uni)} unique stocks · data as of {info['last_date']:%d %b %Y}")
    for w in info["warnings"]:
        print("WARNING:", w)
    summary, _, skipped = run_scan(uni, Params(), prices)
    out = summary[summary["Score"] >= a.min_score] if len(summary) else summary
    out.to_csv(a.out, index=False)
    for s in (5, 4, 3):
        sub = out[out["Score"] == s] if len(out) else out
        print(f"\n=== {s}/5 : {len(sub)} stocks ===")
        if len(sub):
            print(sub[["Symbol", "Close", "RSI", "Failed"]].to_string(index=False))
    if skipped:
        print(f"\nSkipped (no/insufficient data): {len(skipped)}")
    print(f"\nSaved {a.out}")
