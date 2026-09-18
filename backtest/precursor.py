#!/usr/bin/env python3
"""
Precursor analysis: what the tape looks like immediately BEFORE a big up-move.

The question this answers is not "what is a good entry rule" but "what is
measurably different about the hours leading into the start of a pump", so the
conditions can be used as an EARLY-DETECTION signal in a live scanner.

Method
------
1.  Hourly bars for the whole coin basket.

2.  EVENT = a bar t whose close is followed, within the next H hours, by a high
    at least T above it:
            fwd_max[t] = max(high[t+1 .. t+H]) / close[t] - 1  >=  T
    To capture the START of a move and not every hour inside an ongoing rally,
    only the first bar of a cluster is kept: t qualifies only if no bar in
    [t-H, t-1] already qualified. Events are therefore non-overlapping.

3.  For every event, features are measured at the event bar's CLOSE and at lags
    of 1..12 hours BEFORE it. Every feature uses only closed bars at that lag,
    so a live scanner could have computed it in real time.

4.  Two things are reported:
      * DISTRIBUTION — feature values before events vs the unconditional
        baseline of all bars, as medians and as "share of cases above X".
      * DETECTION — for candidate trigger conditions: how often the condition
        fires, and the precision P(move >= T within H | condition) against the
        base rate. Lift = precision / base rate.

Usage:
    python3 backtest/precursor.py --threshold 0.15 --horizon 24
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import load, sma, atr, rsi, FEE_BPS, SLIP_BPS

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

BASE = 168          # 7 days of hourly bars — the "recent normal" baseline
LAGS = [0, 1, 2, 3, 4, 6, 8, 12, 24]


def features(df: pd.DataFrame) -> pd.DataFrame:
    """All features use closed bars only — nothing here sees the future."""
    c, h, l, v = df["close"], df["high"], df["low"], df["quote_volume"]
    f = pd.DataFrame(index=df.index)

    # --- volume ---
    vm = v.rolling(BASE, min_periods=BASE // 2).mean()
    vs = v.rolling(BASE, min_periods=BASE // 2).std()
    f["vol_z"] = (v - vm) / vs.replace(0, np.nan)
    f["vol_ratio"] = v / vm.replace(0, np.nan)
    # volume of the last 4h vs the average 4h block of the last 7 days
    f["vol_ratio_4h"] = v.rolling(4).sum() / (vm * 4).replace(0, np.nan)
    f["vol_slope_6h"] = (v.rolling(6).mean() / v.rolling(24).mean().replace(0, np.nan))
    tm = df["trades"].rolling(BASE, min_periods=BASE // 2).mean()
    ts = df["trades"].rolling(BASE, min_periods=BASE // 2).std()
    f["trades_z"] = (df["trades"] - tm) / ts.replace(0, np.nan)

    # --- order-flow: share of volume that hit the ask (aggressive buying) ---
    tb = df["taker_buy_quote"] / v.replace(0, np.nan)
    f["taker_buy"] = tb
    f["taker_buy_4h"] = (df["taker_buy_quote"].rolling(4).sum()
                         / v.rolling(4).sum().replace(0, np.nan))

    # --- volatility / range compression ---
    a24, a168 = atr(df, 24), atr(df, 168)
    f["atr_ratio"] = a24 / a168.replace(0, np.nan)       # <1 = compression
    f["atr_pct"] = a24 / c                                # ATR as % of price
    mid = sma(c, 24)
    sd = c.rolling(24, min_periods=24).std()
    bw = (2 * sd) / mid.replace(0, np.nan)
    f["bb_width"] = bw
    f["bb_width_pct"] = bw.rolling(24 * 30, min_periods=24 * 7).rank(pct=True)
    rng = (h.rolling(24).max() - l.rolling(24).min()) / c
    f["range24_pct"] = rng.rolling(24 * 30, min_periods=24 * 7).rank(pct=True)

    # --- momentum / position ---
    f["rsi14"] = rsi(c, 14)
    f["ret_1h"] = c.pct_change(1)
    f["ret_4h"] = c.pct_change(4)
    f["ret_24h"] = c.pct_change(24)
    f["ret_72h"] = c.pct_change(72)
    f["ret_168h"] = c.pct_change(168)
    f["dist_24h_high"] = c / h.rolling(24, min_periods=24).max().replace(0, np.nan) - 1
    f["dist_7d_high"] = c / h.rolling(168, min_periods=100).max().replace(0, np.nan) - 1
    f["dist_30d_high"] = c / h.rolling(720, min_periods=400).max().replace(0, np.nan) - 1
    f["above_ema50"] = (c > c.ewm(span=50, adjust=False, min_periods=50).mean()).astype(float)
    f["above_ema200"] = (c > c.ewm(span=200, adjust=False, min_periods=200).mean()).astype(float)
    return f


def find_events(df: pd.DataFrame, thresh: float, horizon: int) -> np.ndarray:
    """Boolean mask of non-overlapping move STARTS."""
    fwd = (df["high"].shift(-1).rolling(horizon, min_periods=1).max()
           .shift(-(horizon - 1)) / df["close"] - 1.0)
    hit = (fwd >= thresh).to_numpy()
    hit = np.nan_to_num(hit, nan=False).astype(bool)
    out = np.zeros(len(hit), bool)
    last = -10 ** 9
    for i in np.flatnonzero(hit):
        if i - last >= horizon:     # first bar of the cluster only
            out[i] = True
            last = i

    # how many hours after the event bar the threshold is actually reached
    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)
    hrs = np.full(len(hit), np.nan)
    for i in np.flatnonzero(out):
        for k in range(1, horizon + 1):
            if i + k < len(h) and h[i + k] / c[i] - 1.0 >= thresh:
                hrs[i] = k
                break

    # what a buyer at this bar's close would actually have after `horizon` hours
    # (close to close), and the worst drawdown suffered on the way
    fwd_ret = np.full(len(hit), np.nan)
    fwd_dd = np.full(len(hit), np.nan)
    lo = df["low"].to_numpy(float)
    n = len(c)
    for i in range(n - horizon):
        fwd_ret[i] = c[i + horizon] / c[i] - 1.0
        fwd_dd[i] = lo[i + 1: i + 1 + horizon].min() / c[i] - 1.0
    return out, fwd.to_numpy(), hrs, fwd_ret, fwd_dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.15)
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args()

    os.makedirs(RESULTS, exist_ok=True)
    data_dir = args.data_dir or os.path.join(HERE, "data", args.interval)
    syms = sorted(f.split("-")[0] for f in os.listdir(data_dir) if f.endswith(".csv"))
    print(f"{len(syms)} symbols @ {args.interval}; "
          f"event = +{args.threshold:.0%} within {args.horizon}h\n")

    ev_rows, base_rows, lag_rows, share_rows = [], [], [], []
    # "how often was the feature ALREADY elevated h hours before the start"
    LAG_TESTS = {"vol_z": [1, 2, 3, 4], "trades_z": [1, 2, 3],
                 "vol_ratio_4h": [1.5, 2.0, 3.0], "rsi14_lt": [40, 45, 50]}
    n_bars = n_events = 0

    for sym in syms:
        df = load(sym, args.interval, data_dir)
        if len(df) < 2000:
            continue
        f = features(df)
        ev, fwd, hrs, fret, fdd = find_events(df, args.threshold, args.horizon)
        valid = f["vol_z"].notna().to_numpy() & np.isfinite(fwd)
        ev = ev & valid
        n_bars += int(valid.sum())
        n_events += int(ev.sum())

        fv = f.to_numpy()
        cols = list(f.columns)
        idx = np.flatnonzero(ev)

        # feature values AT the event bar and at lags before it
        for lag in LAGS:
            src = idx - lag
            src = src[src >= 0]
            if len(src) == 0:
                continue
            block = fv[src]
            for j, cname in enumerate(cols):
                col = block[:, j]
                col = col[np.isfinite(col)]
                if len(col):
                    lag_rows.append({"symbol": sym, "lag": lag, "feature": cname,
                                     "median": float(np.median(col)),
                                     "n": len(col)})
                key = cname if cname in LAG_TESTS else (
                    "rsi14_lt" if cname == "rsi14" else None)
                if key and len(col):
                    for thr in LAG_TESTS[key]:
                        hitn = (col < thr).sum() if key == "rsi14_lt" else (col > thr).sum()
                        share_rows.append({"lag": lag, "feature": key, "thr": thr,
                                           "n": len(col), "hits": int(hitn)})
        # unconditional baseline share, for the same thresholds
        for cname, key in [("vol_z", "vol_z"), ("trades_z", "trades_z"),
                           ("vol_ratio_4h", "vol_ratio_4h"), ("rsi14", "rsi14_lt")]:
            col = f[cname].to_numpy()[valid]
            col = col[np.isfinite(col)]
            for thr in LAG_TESTS[key]:
                hitn = (col < thr).sum() if key == "rsi14_lt" else (col > thr).sum()
                share_rows.append({"lag": -1, "feature": key, "thr": thr,
                                   "n": len(col), "hits": int(hitn)})

        sub = f[ev]
        sub = sub.assign(symbol=sym, fwd_max=fwd[ev], hours_to_move=hrs[ev])
        ev_rows.append(sub)
        b = f[valid]
        base_rows.append(b.assign(symbol=sym, fwd_max=fwd[valid],
                                  fwd_ret=fret[valid], fwd_dd=fdd[valid],
                                  is_event=ev[valid]))
        print(f"  {sym:10s} bars {int(valid.sum()):6d}  events {int(ev.sum()):4d}  "
              f"({ev.sum() / max(valid.sum(), 1):.2%} of bars)")

    E = pd.concat(ev_rows)
    B = pd.concat(base_rows)
    base_rate = B["is_event"].mean()
    print(f"\nTOTAL: {n_bars:,} bars, {n_events:,} move starts  "
          f"(base rate {base_rate:.3%} of bars)\n")

    # ---------------- 1. distribution: events vs baseline ----------------
    ht = E["hours_to_move"].dropna()
    print(f"Hours from the event bar to the +{args.threshold:.0%} print: "
          f"median {ht.median():.0f}h, p25 {ht.quantile(.25):.0f}h, "
          f"p75 {ht.quantile(.75):.0f}h\n")
    feats = [c for c in E.columns
             if c not in ("symbol", "fwd_max", "fwd_ret", "fwd_dd",
                          "is_event", "hours_to_move")]
    dist = []
    for c in feats:
        e, b = E[c].replace([np.inf, -np.inf], np.nan).dropna(), \
               B[c].replace([np.inf, -np.inf], np.nan).dropna()
        if len(e) < 100:
            continue
        dist.append({
            "feature": c,
            "event_median": e.median(), "base_median": b.median(),
            "event_p25": e.quantile(.25), "event_p75": e.quantile(.75),
            "base_p75": b.quantile(.75), "base_p90": b.quantile(.90),
            "share_event_gt_base_p90": float((e > b.quantile(.90)).mean()),
            "share_event_gt_base_p75": float((e > b.quantile(.75)).mean()),
            "n_events": len(e),
        })
    dist = pd.DataFrame(dist).sort_values("share_event_gt_base_p90", ascending=False)
    dist.to_csv(os.path.join(RESULTS, "precursor_distribution.csv"), index=False)
    pd.set_option("display.width", 220)
    print("=== FEATURE AT MOVE START vs BASELINE ===")
    print(dist.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # ---------------- 2. lead time: feature level by lag ----------------
    L = pd.DataFrame(lag_rows)
    piv = (L.groupby(["feature", "lag"])["median"].median().unstack("lag"))
    piv.to_csv(os.path.join(RESULTS, "precursor_by_lag.csv"))
    print("\n=== MEDIAN FEATURE VALUE h HOURS BEFORE THE MOVE START ===")
    print(piv.loc[[c for c in ["vol_z", "vol_ratio", "vol_ratio_4h", "vol_slope_6h",
                               "trades_z", "taker_buy", "taker_buy_4h", "rsi14",
                               "atr_ratio", "bb_width_pct", "dist_24h_high",
                               "dist_7d_high", "ret_24h"] if c in piv.index]]
          .to_string(float_format=lambda x: f"{x:,.3f}"))

    # ---------------- 2b. how early was it already visible ----------------
    SH = pd.DataFrame(share_rows)
    agg = SH.groupby(["feature", "thr", "lag"])[["hits", "n"]].sum()
    agg["share"] = agg["hits"] / agg["n"]
    tbl = agg["share"].unstack("lag")
    tbl = tbl.rename(columns={-1: "baseline"})
    tbl.to_csv(os.path.join(RESULTS, "precursor_lead_time.csv"))
    print("\n=== SHARE OF MOVE-STARTS WHERE THE CONDITION ALREADY HELD, "
          "h HOURS EARLIER ===")
    print("(lag 0 = the move-start bar itself; 'baseline' = share over all bars)")
    print((tbl * 100).to_string(float_format=lambda x: f"{x:,.1f}%"))

    # ---------------- 3. detection rules: precision / lift ----------------
    B = B.replace([np.inf, -np.inf], np.nan)
    rules = {
        # --- single conditions ---
        "vol_z > 1": B["vol_z"] > 1,
        "vol_z > 2": B["vol_z"] > 2,
        "vol_z > 3": B["vol_z"] > 3,
        "vol_z > 4": B["vol_z"] > 4,
        "vol_z > 6": B["vol_z"] > 6,
        "vol_ratio_4h > 3": B["vol_ratio_4h"] > 3,
        "trades_z > 3": B["trades_z"] > 3,
        "trades_z > 4": B["trades_z"] > 4,
        "taker_buy > 0.55 (aggressive buying)": B["taker_buy"] > 0.55,
        "taker_buy < 0.45 (sell-side flush)": B["taker_buy"] < 0.45,
        "bb_width_pct < 0.25 (squeeze)": B["bb_width_pct"] < 0.25,
        "bb_width_pct > 0.75 (already volatile)": B["bb_width_pct"] > 0.75,
        "rsi14 < 40 (oversold)": B["rsi14"] < 40,
        "rsi14 > 60 (overbought)": B["rsi14"] > 60,
        "ret_24h < -0.05 (down day)": B["ret_24h"] < -0.05,
        "dist_24h_high > -0.01 (at 24h high)": B["dist_24h_high"] > -0.01,
        # --- combinations ---
        "V1: vol_z>4 & rsi14<45": (B["vol_z"] > 4) & (B["rsi14"] < 45),
        "V2: vol_z>4 & ret_24h<0": (B["vol_z"] > 4) & (B["ret_24h"] < 0),
        "V3: vol_z>4 & taker_buy<0.48": (B["vol_z"] > 4) & (B["taker_buy"] < 0.48),
        "V4: vol_z>4 & bb_width_pct>0.6": (B["vol_z"] > 4) & (B["bb_width_pct"] > 0.6),
        "V5: vol_z>4 & trades_z>3": (B["vol_z"] > 4) & (B["trades_z"] > 3),
        "V6: vol_z>4 & rsi14<45 & bb_width_pct>0.6":
            (B["vol_z"] > 4) & (B["rsi14"] < 45) & (B["bb_width_pct"] > 0.6),
        "V7: vol_z>6 & rsi14<45 & ret_4h<0":
            (B["vol_z"] > 6) & (B["rsi14"] < 45) & (B["ret_4h"] < 0),
        "V8: vol_z>4 & dist_24h_high<-0.05":
            (B["vol_z"] > 4) & (B["dist_24h_high"] < -0.05),
        # --- the "textbook" breakout rule, for contrast ---
        "X1: vol_z>2 & taker_buy>0.55 & at 24h high":
            (B["vol_z"] > 2) & (B["taker_buy"] > 0.55) & (B["dist_24h_high"] > -0.01),
        "X2: vol_z>2 & 7d-high break":
            (B["vol_z"] > 2) & (B["dist_7d_high"] > -0.005),
    }
    rows = []
    for label, m in rules.items():
        m = m.fillna(False)
        n = int(m.sum())
        if n < 50:
            continue
        sel = B[m]
        prec = sel["is_event"].mean()
        rows.append({
            "rule": label, "fires": n,
            "fires_per_coin_per_month": n / max(len(syms), 1) / (n_bars / max(len(syms), 1) / 720),
            "precision": prec, "base_rate": base_rate, "lift": prec / base_rate,
            "median_fwd_max": sel["fwd_max"].median(),
            # what a buyer at the signal close actually gets after `horizon` h
            "mean_fwd_ret": sel["fwd_ret"].mean(),
            "median_fwd_ret": sel["fwd_ret"].median(),
            "p_fwd_ret_pos": float((sel["fwd_ret"] > 0).mean()),
            "mean_fwd_ret_net": sel["fwd_ret"].mean() - 2 * (FEE_BPS + SLIP_BPS) / 1e4,
            "median_fwd_dd": sel["fwd_dd"].median(),
        })
    det = pd.DataFrame(rows).sort_values("lift", ascending=False)
    det.to_csv(os.path.join(RESULTS, "precursor_rules.csv"), index=False)
    print("\n=== DETECTION RULES: P(+{:.0%} within {}h | rule) ===".format(
        args.threshold, args.horizon))
    print(det.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))

    with open(os.path.join(RESULTS, "precursor_meta.json"), "w") as fh:
        json.dump({"symbols": syms, "n_bars": n_bars, "n_events": n_events,
                   "base_rate": float(base_rate), "threshold": args.threshold,
                   "horizon_hours": args.horizon, "interval": args.interval}, fh, indent=2)
    print(f"\nWrote results to {RESULTS}")


if __name__ == "__main__":
    main()
