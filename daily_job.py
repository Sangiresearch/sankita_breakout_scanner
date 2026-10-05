"""Daily scan job.

Downloads the latest NSE / BSE end-of-day files (only the days not already
cached), runs the screener for all three views (NSE 200, BSE 200, both) and
saves the results in the `results` folder. The app shows these results
instantly.

    python daily_job.py

Run it every weekday evening after the market closes (the exchanges publish
the day's file after the close). See README.md for the automatic schedule.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from exchange_data import prepare
from scan import run_scan
from screener import Params

RESULTS_DIR = Path(os.environ.get("RESULTS_DIR", Path(__file__).parent / "results"))
IST = timezone(timedelta(hours=5, minutes=30))
MODES = ["NSE", "BSE", "BOTH"]


def save_results(mode: str, uni: pd.DataFrame, prices: dict, info: dict, out: Path = RESULTS_DIR) -> dict:
    summary, store, skipped = run_scan(uni, Params(), prices)
    out.mkdir(parents=True, exist_ok=True)
    m = mode.lower()
    summary.to_csv(out / f"{m}_summary.csv", index=False)

    # Price history of every stock scoring 3+ so the app can draw charts
    frames = []
    for _, r in (summary[summary["Score"] >= 3] if len(summary) else summary).iterrows():
        df, _res = store[r["Ticker"]]
        d = df.round(4).rename_axis("Date").reset_index()
        d.insert(0, "Key", r["Ticker"])
        frames.append(d)
    charts = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["Key", "Date", "Open", "High", "Low", "Close", "Volume"])
    charts.to_csv(out / f"{m}_charts.csv", index=False)

    meta = {
        "mode": mode,
        "generated_at": datetime.now(IST).isoformat(timespec="minutes"),
        "data_date": str(pd.Timestamp(info["last_date"]).date()),
        "n_universe": int(len(uni)),
        "n_scanned": int(len(summary)),
        "n_skipped": int(len(skipped)),
        "warnings": info["warnings"],
    }
    (out / f"{m}_meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def _progress_printer():
    """Prints a progress line each time another 25% is reached."""
    last = {"step": -1}

    def show(frac: float, text: str) -> None:
        step = int(frac * 4)
        if step != last["step"]:
            last["step"] = step
            print(f"  {text}", flush=True)
    return show


def main() -> int:
    failures = []
    for mode in MODES:
        print(f"== {mode} ==", flush=True)
        try:
            uni, prices, info = prepare(mode, progress=_progress_printer())
            meta = save_results(mode, uni, prices, info)
            print(f"  saved: data as of {meta['data_date']}, "
                  f"{meta['n_scanned']} of {meta['n_universe']} stocks scanned", flush=True)
            for w in info["warnings"]:
                print("  WARNING:", w, flush=True)
        except Exception:  # noqa: BLE001
            failures.append(mode)
            print(f"  FAILED: {mode}", flush=True)
            traceback.print_exc()
    if failures:
        print("Failed: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
