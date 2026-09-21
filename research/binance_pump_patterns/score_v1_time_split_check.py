#!/usr/bin/env python3
"""Time-split sanity check for score_v1.py's SCORE thresholds.

score_v1's report is explicit that its own numbers are in-sample: the rubric
was written looking at the whole dataset, then evaluated on that same
dataset. This script does the cheapest available check on that -- split the
already-scored events by time (score_v1's formula has no fitted parameters,
so no refitting happens here) and see whether the SCORE-vs-REAL_CONTINUATION
relationship, and the individual feature medians behind it, hold up on the
period the rubric's thresholds were not eyeballed against as directly.

This is NOT a substitute for more months of data or a proper walk-forward
validation -- it's a quick regression check to run before touching the live
scanner's SCORE weights in index.html.

    python score_v1_time_split_check.py --in score_v1_results/score_v1_events.csv --split 2026-07-01
"""
from __future__ import annotations

import argparse
import numpy as np
import pandas as pd

FEATURES = ["close_pos", "body_pct", "rvol", "range12_pct", "taker_buy_share", "vol_accel_1h"]
THRESHOLDS = (40, 50, 60, 70, 80)


def threshold_table(sub: pd.DataFrame, base_ref: float) -> pd.DataFrame:
    base = (sub.label == "REAL_CONTINUATION").mean()
    n_real = int((sub.label == "REAL_CONTINUATION").sum())
    rows = [{
        "thr": "all", "n": len(sub), "real_rate": base,
        "lift_vs_own_base": 1.0,
        "lift_vs_ref_base": base / base_ref if base_ref else np.nan,
        "recall": 1.0,
    }]
    for t in THRESHOLDS:
        s = sub[sub.score >= t]
        if s.empty:
            rows.append({"thr": f">={t}", "n": 0, "real_rate": np.nan,
                         "lift_vs_own_base": np.nan, "lift_vs_ref_base": np.nan, "recall": np.nan})
            continue
        r = int((s.label == "REAL_CONTINUATION").sum())
        rate = r / len(s)
        rows.append({
            "thr": f">={t}", "n": len(s), "real_rate": rate,
            "lift_vs_own_base": rate / base if base else np.nan,
            "lift_vs_ref_base": rate / base_ref if base_ref else np.nan,
            "recall": r / n_real if n_real else np.nan,
        })
    return pd.DataFrame(rows)


def feature_medians(sub: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in FEATURES if c in sub.columns]
    return sub.groupby("label")[cols].median()


def compression_check(sub: pd.DataFrame, ceiling: float) -> tuple[int, float, int, float]:
    tight = sub[sub.range12_pct <= ceiling]
    wide = sub[sub.range12_pct > ceiling]
    tr = (tight.label == "REAL_CONTINUATION").mean() if len(tight) else float("nan")
    wr = (wide.label == "REAL_CONTINUATION").mean() if len(wide) else float("nan")
    return len(tight), tr, len(wide), wr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="score_v1_results/score_v1_events.csv",
                     help="score_v1_events.csv from score_v1.py (already has a score column)")
    ap.add_argument("--split", default=None,
                     help="UTC date splitting early/held-out periods, e.g. 2026-07-01. "
                          "Defaults to the midpoint of the data's time range.")
    ap.add_argument("--compression-ceiling", type=float, default=4.0,
                     help="range12_pct threshold below which a base counts as 'tight compression'")
    args = ap.parse_args()

    df = pd.read_csv(args.in_path)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    df = df.sort_values("time_utc").reset_index(drop=True)

    if args.split:
        split = pd.Timestamp(args.split, tz="UTC")
    else:
        split = df.time_utc.min() + (df.time_utc.max() - df.time_utc.min()) / 2

    early = df[df.time_utc < split]
    late = df[df.time_utc >= split]
    base_early = (early.label == "REAL_CONTINUATION").mean() if len(early) else float("nan")
    base_late = (late.label == "REAL_CONTINUATION").mean() if len(late) else float("nan")

    print(f"split at {split}")
    print(f"\nEARLY n={len(early)} base_real_rate={base_early:.3f}")
    print(threshold_table(early, base_early).to_string(index=False))
    print(f"\nLATE (held out) n={len(late)} base_real_rate={base_late:.3f}")
    print(threshold_table(late, base_early).to_string(index=False))

    print("\n--- feature medians by label, EARLY ---")
    print(feature_medians(early).to_string())
    print("\n--- feature medians by label, LATE (held out) ---")
    print(feature_medians(late).to_string())

    print("\n--- pre-impulse compression (range12_pct) check ---")
    for name, sub in (("EARLY", early), ("LATE", late)):
        nt, tr, nw, wr = compression_check(sub, args.compression_ceiling)
        print(f"{name}: range12<={args.compression_ceiling}% n={nt} real_rate={tr:.3f} | "
              f"range12>{args.compression_ceiling}% n={nw} real_rate={wr:.3f}")


if __name__ == "__main__":
    main()
