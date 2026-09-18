#!/usr/bin/env python3
"""
Rolling walk-forward validation — the main validation script.

Why this and not a single train/test split: one split gives you exactly one
out-of-sample number, which is itself a coin flip. Here the whole history is cut
into consecutive folds; parameters are re-tuned on each fold's in-sample window
and then traded blind on the following out-of-sample year. Stitching those
out-of-sample years together produces a single equity curve that no parameter
was ever fitted to. That curve is the honest estimate of what the rule would
have done.

Folds (daily bars, anchored-expanding in-sample):
    IS 2018-01-01..2020-12-31 -> OOS 2021
    IS 2018-01-01..2021-12-31 -> OOS 2022
    ... through OOS 2026 (data ends 2026-08-31)

Three things are reported for every family:
  1. WF   — the stitched walk-forward curve (re-tuned each fold).
  2. FIX  — one fixed, central parameter set traded over the whole period, never
            tuned at all. If FIX beats WF, the tuning is noise.
  3. B&H  — equal-weight buy & hold of the same basket over the same bars.

Usage:
    python3 backtest/walkforward.py --workers 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine as E
import strategies as S
from engine import (load, load_basket, run, metrics, basket_equity,
                    ew_portfolio, period_id, BARS_PER_YEAR)
from strategies import STRATEGIES, grid, valid
from xsec import run_xsec, xsec_grid

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

# Universe (54 USDT spot pairs).
#
# SURVIVORSHIP BIAS: picking today's liquid coins and backtesting them in 2021
# is cheating, because we already know they survived. To blunt that, the list
# deliberately includes names that later collapsed or were delisted from Binance
# spot: LUNAUSDT (Terra, May 2022), FTTUSDT (FTX, Nov 2022), WAVESUSDT, SRMUSDT,
# RENUSDT, OMGUSDT, EOSUSDT, FTMUSDT, MKRUSDT. Residual bias remains — this is
# not a point-in-time reconstruction of the full Binance listing — so the
# absolute return numbers should be read as optimistic.
SYMBOLS = [
    # large / established
    "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT",
    "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT", "ATOMUSDT",
    "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT",
    "UNIUSDT", "AAVEUSDT", "FILUSDT", "ETCUSDT", "BCHUSDT", "ALGOUSDT",
    "XLMUSDT", "VETUSDT", "HBARUSDT", "ICPUSDT", "EGLDUSDT", "XTZUSDT",
    # mid-cap / thematic
    "SANDUSDT", "MANAUSDT", "GALAUSDT", "APEUSDT", "CRVUSDT", "DYDXUSDT",
    "CHZUSDT", "THETAUSDT", "AXSUSDT", "GRTUSDT", "ENJUSDT", "RUNEUSDT",
    "SNXUSDT", "SUSHIUSDT", "1INCHUSDT",
    # later delisted / collapsed - included on purpose
    "LUNAUSDT", "FTTUSDT", "WAVESUSDT", "SRMUSDT", "RENUSDT", "OMGUSDT",
    "EOSUSDT", "FTMUSDT", "MKRUSDT",
]

WF_START = "2018-01-01"
FOLDS = [  # (is_start, is_end, oos_start, oos_end)
    (WF_START, "2020-12-31", "2021-01-01", "2021-12-31"),
    (WF_START, "2021-12-31", "2022-01-01", "2022-12-31"),
    (WF_START, "2022-12-31", "2023-01-01", "2023-12-31"),
    (WF_START, "2023-12-31", "2024-01-01", "2024-12-31"),
    (WF_START, "2024-12-31", "2025-01-01", "2025-12-31"),
    (WF_START, "2025-12-31", "2026-01-01", "2026-12-31"),
]

# Fixed "sensible default" parameter sets — chosen for being textbook-central,
# NOT by searching. These are the untuned control group.
FIXED = {
    "ema_atr":      {"fast": 20, "slow": 100, "atr_k": 3.0, "regime": 0, "btc_regime": 200, "sizing": "voltarget"},
    "donchian":     {"n_in": 55, "n_out": 20, "atr_k": 3.0, "regime": 0, "btc_regime": 200, "sizing": "voltarget"},
    "rsi_bb":       {"bb_n": 20, "bb_k": 2.5, "rsi_buy": 30, "atr_k": 2.0, "max_bars": 10, "regime": 0, "btc_regime": 0, "sizing": "full"},
    "vol_momentum": {"lookback": 90, "thresh": 0.0, "atr_k": 4.0, "regime": 0, "btc_regime": 200, "sizing": "voltarget"},
    "vol_breakout": {"n_in": 40, "n_out": 20, "v_mult": 2.0, "v_n": 20, "atr_k": 3.0, "regime": 0, "btc_regime": 200, "sizing": "full"},
    "xsec_mom":     {"lookback": 90, "k": 5, "rebalance": 14, "thresh": 0.0, "btc_regime": 200, "inv_vol": False},
}

MIN_TRADES_IS = 40
MIN_ACTIVE = 0.25       # >= 25% of in-sample bars with capital at risk
ENSEMBLE_N = 5          # trade the top-5 in-sample parameter sets, equal weight
_DATA: dict[str, pd.DataFrame] = {}
_BTC: pd.DataFrame | None = None
_INTERVAL = "1d"


def _init(interval, data_dir):
    global _DATA, _BTC, _INTERVAL
    _INTERVAL = interval
    _DATA = load_basket(SYMBOLS, interval, data_dir)
    try:
        _BTC = load("BTCUSDT", interval, data_dir)
    except FileNotFoundError:
        _BTC = None
    S.set_btc(_BTC)


def _utc(t):
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _win(s, a, b):
    idx = s.index
    return s[(idx >= _utc(a)) & (idx <= _utc(b))]


def _windows():
    w = {}
    for i, (isa, isb, osa, osb) in enumerate(FOLDS):
        w[f"is{i}"] = (isa, isb)
        w[f"oos{i}"] = (osa, osb)
    w["full"] = (WF_START, "2026-12-31")
    return w


def eval_job(args, keep_curves: bool = False):
    """
    Evaluate one (strategy, params) on every fold window.

    Speed matters here (thousands of combos x 54 coins x 13 windows), so the
    per-coin sleeve returns are built ONCE as a matrix and every window is then a
    slice of it. That gives one `metrics` call per window instead of one per
    coin per window, which is where the naive version spent all its time.
    """
    name, params = args
    bpy = BARS_PER_YEAR[_INTERVAL]
    res = {"strategy": name, "params_json": json.dumps(params, sort_keys=True)}

    if name == "xsec_mom":
        eq, _, to = run_xsec(_DATA, params, bpy, _BTC)
        R = pd.DataFrame({"_port": eq.pct_change()})
        entry_times = None
        turn = to
    else:
        fn = STRATEGIES[name]
        cols, ets = {}, []
        for sym, df in _DATA.items():
            sig = fn(df, params, bpy)
            equity, trades = run(df, sig["entry"], sig["exit"], sig.get("stop_dist"),
                                 sig.get("size"), trail=sig.get("trail", True),
                                 max_bars=sig.get("max_bars"))
            cols[sym] = equity.pct_change()
            if len(trades):
                ets.append(pd.DatetimeIndex(trades["entry_time"]).tz_localize(None)
                           .to_numpy())
        R = pd.DataFrame(cols)
        entry_times = (np.sort(np.concatenate(ets)) if ets
                       else np.array([], "datetime64[ns]"))
        turn = None

    idx = R.index.tz_localize(None).to_numpy()
    arr = R.to_numpy(float)
    pid = period_id(R.index)

    for tag, (a, b) in _windows().items():
        lo_t = np.datetime64(pd.Timestamp(a))
        hi_t = np.datetime64(pd.Timestamp(b))
        lo = np.searchsorted(idx, lo_t)
        hi = np.searchsorted(idx, hi_t, "right")
        if hi - lo < 60:
            continue
        sub = arr[lo:hi]
        port = ew_portfolio(sub, pid[lo:hi])   # monthly-rebalanced equal weight
        port[0] = 0.0
        beq = pd.Series(np.cumprod(1.0 + port), index=R.index[lo:hi])
        m = metrics(beq, pd.DataFrame(columns=["ret", "bars"]), bpy)
        # share of bars with capital actually at risk — stops the parameter
        # search from "winning" by selecting a variant that barely trades
        m["active"] = float(np.mean(port != 0.0))

        if name == "xsec_mom":
            m["trades"] = int(round(float(turn.to_numpy()[lo:hi].sum()) * 100))
            m["coins"] = 1
            m["frac_pos"] = float(beq.iloc[-1] > 1.0)
        else:
            m["trades"] = int(np.searchsorted(entry_times, hi_t, "right")
                              - np.searchsorted(entry_times, lo_t))
            live = np.isfinite(sub).sum(axis=0) >= 60
            tot = np.nanprod(1.0 + np.where(np.isfinite(sub), sub, 0.0), axis=0)
            m["coins"] = int(live.sum())
            m["frac_pos"] = float(np.mean(tot[live] > 1.0)) if live.any() else np.nan
        res[tag] = m
        if keep_curves:
            res[f"{tag}_ret"] = beq
            res[f"{tag}_rets"] = pd.Series(port, index=R.index[lo:hi])
    return res


def eval_job_curves(args):
    return eval_job(args, keep_curves=True)


def stitch(segments):
    """Chain consecutive out-of-sample equity segments into one curve."""
    parts, level = [], 1.0
    for seg in segments:
        s = seg / seg.iloc[0] * level
        parts.append(s)
        level = s.iloc[-1]
    return pd.concat(parts).sort_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    os.makedirs(RESULTS, exist_ok=True)
    _init(args.interval, args.data_dir)
    bpy = BARS_PER_YEAR[args.interval]
    print(f"Loaded {len(_DATA)} coins, BTC={'yes' if _BTC is not None else 'no'}")

    jobs = []
    for name in STRATEGIES:
        jobs += [(name, p) for p in grid(name) if valid(name, p)]
    jobs += [("xsec_mom", p) for p in xsec_grid()]
    print(f"Evaluating {len(jobs)} combos across {len(FOLDS)} folds ...")

    results = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init,
                             initargs=(args.interval, args.data_dir)) as pool:
        for i, r in enumerate(pool.map(eval_job, jobs, chunksize=2), 1):
            results.append(r)
            if i % 250 == 0:
                print(f"  {i}/{len(jobs)}")

    # ------------- dump the whole scalar grid for the record -------------
    grid_rows = []
    for r in results:
        row = {"strategy": r["strategy"], "params": r["params_json"]}
        for tag in _windows():
            m = r.get(tag)
            if not m:
                continue
            row[f"{tag}_cagr"] = m["cagr"]
            row[f"{tag}_sharpe"] = m["sharpe"]
            row[f"{tag}_maxdd"] = m["max_dd"]
            row[f"{tag}_trades"] = m["trades"]
        grid_rows.append(row)
    pd.DataFrame(grid_rows).to_csv(
        os.path.join(RESULTS, f"grid_all_{args.interval}.csv"), index=False)

    # ------------- benchmark: equal-weight buy & hold -------------
    bh_curves = {}
    cost = (E.FEE_BPS + E.SLIP_BPS) / 10_000.0
    for sym, df in _DATA.items():
        c = _win(df["close"], WF_START, "2026-12-31")
        if len(c) < 60:
            continue
        bh_curves[sym] = c / c.iloc[0] * (1 - cost) ** 2
    bh_full = basket_equity(bh_curves)

    # ------------- pass 2: re-run only the selected combos, keeping curves -------------
    #
    # Selection rule, fixed before any out-of-sample number was looked at:
    #   * eligible = enough trades AND capital at risk on >= MIN_ACTIVE of bars,
    #     so a parameter set cannot "win" the search by sitting in cash;
    #   * rank eligible sets by IN-SAMPLE Sharpe;
    #   * trade an equal-weight ENSEMBLE of the top ENSEMBLE_N of them.
    # Ensembling neighbouring parameters is a standard overfitting brake: if the
    # single best in-sample setting is a fluke, its neighbours dilute it.
    selected: dict[tuple[str, int], list[str]] = {}
    for name in list(STRATEGIES) + ["xsec_mom"]:
        fam = [r for r in results if r["strategy"] == name]
        for i in range(len(FOLDS)):
            cand = [r for r in fam
                    if f"is{i}" in r and f"oos{i}" in r
                    and (r[f"is{i}"].get("trades") or 0) >= MIN_TRADES_IS
                    and (r[f"is{i}"].get("active") or 0) >= MIN_ACTIVE
                    and not pd.isna(r[f"is{i}"]["sharpe"])]
            if not cand:
                continue
            cand.sort(key=lambda r: r[f"is{i}"]["sharpe"], reverse=True)
            selected[(name, i)] = [r["params_json"] for r in cand[:ENSEMBLE_N]]
    need = sorted({(n, pj) for (n, _), pjs in selected.items() for pj in pjs}
                  | {(n, json.dumps(FIXED[n], sort_keys=True))
                     for n in list(STRATEGIES) + ["xsec_mom"]})
    print(f"Re-running {len(need)} selected combos with equity curves ...")
    curve_jobs = [(n, json.loads(pj)) for n, pj in need]
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init,
                             initargs=(args.interval, args.data_dir)) as pool:
        curved = dict(zip(need, pool.map(eval_job_curves, curve_jobs)))

    rows = []
    wf_curves = {}
    for name in list(STRATEGIES) + ["xsec_mom"]:
        fam = [r for r in results if r["strategy"] == name]

        # ---- 1. walk-forward: re-tune on each fold's IS, trade the next OOS ----
        segs, picks, fold_rows = [], [], []
        for i in range(len(FOLDS)):
            if (name, i) not in selected:
                continue
            pjs = selected[(name, i)]
            members = [curved[(name, pj)][f"oos{i}_rets"] for pj in pjs]
            ens = pd.concat(members, axis=1).mean(axis=1).fillna(0.0)
            ens.iloc[0] = 0.0
            seg = (1.0 + ens).cumprod()
            segs.append(seg)
            picks.append(pjs)
            o = metrics(seg, pd.DataFrame(columns=["ret", "bars"]), bpy)
            o["trades"] = int(np.mean([curved[(name, pj)][f"oos{i}"]["trades"]
                                       for pj in pjs]))
            o["frac_pos"] = float(np.mean([curved[(name, pj)][f"oos{i}"]["frac_pos"]
                                           for pj in pjs]))
            b = _win(bh_full, *FOLDS[i][2:])
            bm = metrics(b / b.iloc[0], pd.DataFrame(columns=["ret", "bars"]), bpy)
            fold_rows.append({
                "strategy": name, "fold": i, "oos_from": FOLDS[i][2][:7],
                "oos_to": FOLDS[i][3][:7], "params": json.dumps(pjs),
                "oos_cagr": o["cagr"], "oos_sharpe": o["sharpe"],
                "oos_maxdd": o["max_dd"], "oos_trades": o["trades"],
                "oos_frac_coins_pos": o.get("frac_pos"),
                "bh_cagr": bm["cagr"], "bh_sharpe": bm["sharpe"], "bh_maxdd": bm["max_dd"],
            })
        if not segs:
            continue
        wf = stitch(segs)
        wfm = metrics(wf, pd.DataFrame(columns=["ret", "bars"]), bpy)
        wf_curves[name] = wf

        # ---- 2. fixed untuned parameters over the same span ----
        fxr = curved[(name, json.dumps(FIXED[name], sort_keys=True))]
        fxeq = _win(fxr["full_ret"], wf.index[0], wf.index[-1])
        fxeq = fxeq / fxeq.iloc[0]
        fxm = metrics(fxeq, pd.DataFrame(columns=["ret", "bars"]), bpy)

        # ---- 3. grid-wide OOS robustness (no selection at all) ----
        all_oos = []
        for r in fam:
            sh = [r[f"oos{i}"]["sharpe"] for i in range(len(FOLDS))
                  if f"oos{i}" in r and not pd.isna(r[f"oos{i}"]["sharpe"])]
            if sh:
                all_oos.append(np.mean(sh))

        rows.append({
            "strategy": name,
            "wf_cagr": wfm["cagr"], "wf_sharpe": wfm["sharpe"],
            "wf_sortino": wfm["sortino"], "wf_maxdd": wfm["max_dd"],
            "wf_calmar": wfm["calmar"], "wf_total_return": wfm["total_return"],
            "fix_cagr": fxm["cagr"], "fix_sharpe": fxm["sharpe"],
            "fix_maxdd": fxm["max_dd"], "fix_total_return": fxm["total_return"],
            "grid_median_oos_sharpe": float(np.median(all_oos)) if all_oos else np.nan,
            "grid_frac_positive": float(np.mean([a > 0 for a in all_oos])) if all_oos else np.nan,
            "n_params": len(fam),
        })
        pd.DataFrame(fold_rows).to_csv(
            os.path.join(RESULTS, f"folds_{name}_{args.interval}.csv"), index=False)

    bhw = _win(bh_full, min(c.index[0] for c in wf_curves.values()),
               max(c.index[-1] for c in wf_curves.values()))
    bhw = bhw / bhw.iloc[0]
    bhm = metrics(bhw, pd.DataFrame(columns=["ret", "bars"]), bpy)
    rows.append({"strategy": "BUY_AND_HOLD", "wf_cagr": bhm["cagr"],
                 "wf_sharpe": bhm["sharpe"], "wf_sortino": bhm["sortino"],
                 "wf_maxdd": bhm["max_dd"], "wf_calmar": bhm["calmar"],
                 "wf_total_return": bhm["total_return"]})

    summ = pd.DataFrame(rows).sort_values("wf_sharpe", ascending=False)
    summ.to_csv(os.path.join(RESULTS, f"walkforward_summary_{args.interval}.csv"), index=False)
    pd.DataFrame({k: v for k, v in wf_curves.items()} | {"BUY_AND_HOLD": bhw}
                 ).resample("W").last().to_csv(
        os.path.join(RESULTS, f"walkforward_curves_{args.interval}.csv"))

    pd.set_option("display.width", 250)
    print("\n================ WALK-FORWARD (stitched out-of-sample) ================")
    print(summ.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))
    print(f"\nWrote results to {RESULTS}")


if __name__ == "__main__":
    main()
