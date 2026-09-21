#!/usr/bin/env python3
"""SCORE v1 for thin USD-M 5m impulses. In-sample diagnostic, not a live signal.

Reads runup_events.csv produced by binance_runup_research.py and scores each
event with a hand-built rubric (impulse size, candle shape, RVOL, liquidity,
combo bonus, penalties). Compares SCORE thresholds against the research
REAL_CONTINUATION label to see whether the rubric actually separates real
run-ups from fake spikes -- see score_v1_results/ for a real run's output and
score_v1_time_split_check.py for an out-of-sample sanity check on it.

    python score_v1.py --in ../binance_results/runup_events.csv --out ./score_v1_results
"""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def score_impulse(ret5: float) -> tuple[float, str]:
    if pd.isna(ret5):
        return 0.0, "ret5=na"
    if ret5 < 5:
        pts = 4.0 if ret5 >= 3 else 0.0
        return pts, f"ret5={ret5:.2f}<5 (high-wick path)"
    if ret5 < 7:
        pts = 8 + (ret5 - 5) / 2 * 8  # 8..16
        return pts, f"ret5={ret5:.2f} modest"
    if ret5 < 10:
        pts = 16 + (ret5 - 7) / 3 * 10  # 16..26
        return pts, f"ret5={ret5:.2f} strong"
    return 30.0, f"ret5={ret5:.2f} very strong"


def score_shape(close_pos: float, body_pct: float) -> tuple[float, str]:
    if pd.isna(close_pos):
        close_pos = 0.0
    if pd.isna(body_pct):
        body_pct = 0.0
    if close_pos < 0.50:
        cpts = 0.0
    elif close_pos < 0.80:
        cpts = (close_pos - 0.50) / 0.30 * 8
    elif close_pos < 0.90:
        cpts = 8 + (close_pos - 0.80) / 0.10 * 4
    else:
        cpts = 15.0
    if body_pct < 50:
        bpts = 0.0
    elif body_pct < 70:
        bpts = (body_pct - 50) / 20 * 4
    elif body_pct < 85:
        bpts = 4 + (body_pct - 70) / 15 * 4
    else:
        bpts = 10.0
    return cpts + bpts, f"close_pos={close_pos:.2f}->{cpts:.1f}, body={body_pct:.1f}->{bpts:.1f}"


def score_rvol(rvol: float) -> tuple[float, str]:
    if pd.isna(rvol) or rvol < 3:
        return 0.0, f"rvol={rvol}"
    if rvol < 50:
        pts = 4.0
    elif rvol < 100:
        pts = 8.0
    elif rvol < 250:
        pts = 14.0
    elif rvol < 600:
        pts = 20.0
    else:
        pts = 16.0  # extreme spike is not uniquely REAL
    return pts, f"rvol={rvol:.1f}"


def score_liq(q5: float, vol24: float) -> tuple[float, str]:
    if pd.isna(q5):
        q5 = 0.0
    if pd.isna(vol24):
        vol24 = 0.0
    if q5 < 50_000:
        pts = 2.0
    elif q5 < 150_000:
        pts = 5.0
    elif q5 < 400_000:
        pts = 8.0
    else:
        pts = 10.0
    note = f"q5={q5:.0f}"
    if vol24 < 200_000:
        pts = min(pts, 4.0)
        note += ", very thin 24h cap"
    return pts, note


def combo_bonus(ret5, close_pos, body_pct) -> tuple[float, str]:
    if (ret5 >= 7) and (close_pos >= 0.80) and (body_pct >= 70):
        return 15.0, "combo ret5>=7 & close>=0.80 & body>=70"
    if (ret5 >= 7) and (close_pos >= 0.80):
        return 8.0, "combo ret5>=7 & close>=0.80"
    if (ret5 >= 7) and (body_pct >= 70):
        return 6.0, "combo ret5>=7 & body>=70"
    return 0.0, "no combo"


def penalties(row, recent_fails: int) -> tuple[float, list[str]]:
    pts = 0.0
    why = []
    va = row.get("vol_accel_1h", np.nan)
    body = row.get("body_pct", np.nan)
    cp = row.get("close_pos", np.nan)
    r12 = row.get("range12_pct", np.nan)
    if pd.notna(cp) and cp < 0.50:
        pts -= 10
        why.append("close in lower half −10")
    if pd.notna(va) and pd.notna(body) and va >= 80 and body < 70:
        pts -= 8
        why.append("vol_accel>=80 without wide body −8")
    if pd.notna(va) and pd.notna(cp) and va >= 120 and cp < 0.80:
        pts -= 6
        why.append("vol_accel>=120 and weak close −6")
    if pd.notna(r12) and r12 <= 4:
        pts -= 4
        why.append("12h compression ≤4% −4")
    if recent_fails >= 2:
        pts -= 8
        why.append(f"{recent_fails} prior FAIL 7d −8")
    elif recent_fails == 1:
        pts -= 4
        why.append("1 prior FAIL 7d −4")
    return pts, why


def prior_fails(df: pd.DataFrame) -> list[int]:
    out = []
    by = {}
    for _, r in df.iterrows():
        sym = r.symbol
        t = r.time_utc
        hist = by.get(sym, [])
        n = sum(1 for ht, lab in hist if (t - ht).total_seconds() <= 7 * 86400 and lab == "FAILED_OR_PARTIAL")
        out.append(n)
        hist.append((t, r.label))
        by[sym] = hist
    return out


def apply_score(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["time_utc"] = pd.to_datetime(x["time_utc"], utc=True)
    x = x.sort_values(["time_utc", "symbol"]).reset_index(drop=True)
    x["prior_fail_7d"] = prior_fails(x)
    rows = []
    for _, r in x.iterrows():
        i_pts, i_why = score_impulse(r.ret5_pct)
        s_pts, s_why = score_shape(r.close_pos, r.body_pct)
        v_pts, v_why = score_rvol(r.rvol)
        l_pts, l_why = score_liq(r.quote_volume_5m, r.volume24_usd)
        b_pts, b_why = combo_bonus(r.ret5_pct, r.close_pos, r.body_pct)
        p_pts, p_why = penalties(r, int(r.prior_fail_7d))
        raw = i_pts + s_pts + v_pts + l_pts + b_pts + p_pts
        score = int(round(clip(raw, 0, 100)))
        rows.append({
            "score": score,
            "pts_impulse": round(i_pts, 2),
            "pts_shape": round(s_pts, 2),
            "pts_rvol": round(v_pts, 2),
            "pts_liq": round(l_pts, 2),
            "pts_combo": round(b_pts, 2),
            "pts_penalty": round(p_pts, 2),
            "why": " | ".join([i_why, s_why, v_why, l_why, b_why] + p_why),
        })
    return pd.concat([x, pd.DataFrame(rows)], axis=1)


def threshold_table(scored: pd.DataFrame) -> pd.DataFrame:
    base = (scored.label == "REAL_CONTINUATION").mean()
    rows = []
    n_all = len(scored)
    n_real = int((scored.label == "REAL_CONTINUATION").sum())
    rows.append({
        "threshold": "all",
        "n": n_all,
        "share_of_events": 1.0,
        "real": n_real,
        "real_rate": base,
        "lift": 1.0,
        "recall_real": 1.0,
        "mfe2h_median": float(scored.mfe_2h_pct.median()),
        "mae2h_median": float(scored.mae_2h_pct.median()),
        "mfe4h_median": float(scored.mfe_4h_pct.median()),
    })
    for thr in (40, 50, 60, 70, 80, 90):
        sub = scored[scored.score >= thr]
        if sub.empty:
            rows.append({
                "threshold": f">={thr}",
                "n": 0, "share_of_events": 0.0, "real": 0, "real_rate": np.nan,
                "lift": np.nan, "recall_real": 0.0,
                "mfe2h_median": np.nan, "mae2h_median": np.nan, "mfe4h_median": np.nan,
            })
            continue
        real = int((sub.label == "REAL_CONTINUATION").sum())
        rate = real / len(sub)
        rows.append({
            "threshold": f">={thr}",
            "n": int(len(sub)),
            "share_of_events": len(sub) / n_all,
            "real": real,
            "real_rate": rate,
            "lift": rate / base if base else np.nan,
            "recall_real": real / n_real if n_real else np.nan,
            "mfe2h_median": float(sub.mfe_2h_pct.median()),
            "mae2h_median": float(sub.mae_2h_pct.median()),
            "mfe4h_median": float(sub.mfe_4h_pct.median()),
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="binance_results/runup_events.csv",
                     help="runup_events.csv from binance_runup_research.py")
    ap.add_argument("--out", dest="out_dir", default="score_v1_results")
    args = ap.parse_args()
    in_path = Path(args.in_path)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(in_path)
    scored = apply_score(raw)
    scored.to_csv(out / "score_v1_events.csv", index=False)
    thr = threshold_table(scored)
    thr.to_csv(out / "score_v1_thresholds.csv", index=False)

    first = scored.sort_values("time_utc").drop_duplicates("symbol", keep="first")
    thr_first = threshold_table(first)
    thr_first.to_csv(out / "score_v1_thresholds_first_per_symbol.csv", index=False)

    bins = list(range(0, 110, 10))
    scored["score_bin"] = pd.cut(scored.score, bins=bins, right=False, include_lowest=True)
    dec = scored.groupby("score_bin", observed=False).agg(
        n=("score", "size"),
        real=("label", lambda s: int((s == "REAL_CONTINUATION").sum())),
        real_rate=("label", lambda s: float((s == "REAL_CONTINUATION").mean()) if len(s) else np.nan),
        mfe2h=("mfe_2h_pct", "median"),
        mae2h=("mae_2h_pct", "median"),
    ).reset_index()
    dec.to_csv(out / "score_v1_bins.csv", index=False)

    by_sym = scored.groupby("symbol").agg(
        n=("score", "size"),
        real=("label", lambda s: int((s == "REAL_CONTINUATION").sum())),
        score_median=("score", "median"),
        mfe2h_median=("mfe_2h_pct", "median"),
    ).sort_values(["n", "real"], ascending=False)
    by_sym.to_csv(out / "score_v1_by_symbol.csv")

    print("n", len(scored), "real", int((scored.label == "REAL_CONTINUATION").sum()))
    print("score describe\n", scored.score.describe())
    print("score by label\n", scored.groupby("label").score.describe())
    print("thresholds\n", thr.to_string(index=False))
    print("first-per-symbol thresholds\n", thr_first.to_string(index=False))
    print("bins\n", dec.to_string(index=False))


if __name__ == "__main__":
    main()
