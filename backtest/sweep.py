#!/usr/bin/env python3
"""
In-sample tuning + out-of-sample validation sweep.

Protocol
--------
1. Every (strategy, parameter set) is run once per coin over that coin's full
   history. Because every indicator is backward-looking and the engine executes
   at the next bar's open, running the whole history and then slicing windows is
   equivalent to running the windows separately, but keeps the indicators warm.

2. IN-SAMPLE window (parameters are chosen here, and only here):
       2021-01-01 .. 2023-12-31   (2021 bull, 2022 bear, 2023 recovery)

3. OUT-OF-SAMPLE window (never used for any selection decision):
       2024-01-01 .. end of data  (2024-2026)

4. Selection rule, fixed BEFORE looking at OOS: among parameter sets that trade
   enough (>= MIN_TRADES_IS basket-wide and >= MIN_COINS coins with >= 3 trades),
   pick the one with the best IN-SAMPLE basket Sharpe. Then report its OOS.
   The top-10 IS parameter sets' OOS results are also reported, so the reader can
   see whether the winner was a lucky draw or the whole neighbourhood works.

Usage:
    python3 backtest/sweep.py --interval 1d
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
from engine import load_basket, run, metrics, buy_hold, basket_equity, BARS_PER_YEAR
from strategies import STRATEGIES, grid, valid

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

IS_START, IS_END = "2021-01-01", "2023-12-31"
OOS_START = "2024-01-01"

MIN_TRADES_IS = 60      # basket-wide minimum before a Sharpe is believable
MIN_COINS = 10          # coins that must have >= 3 in-sample trades

_DATA: dict[str, pd.DataFrame] = {}
_INTERVAL = "1d"


def _slice(s, a=None, b=None):
    idx = s.index
    m = np.ones(len(idx), bool)
    if a:
        m &= idx >= pd.Timestamp(a, tz="UTC")
    if b:
        m &= idx <= pd.Timestamp(b, tz="UTC")
    return s[m]


def _window_stats(equity, trades, a, b, bpy):
    eq = _slice(equity, a, b)
    if len(eq) < 30:
        return None, None
    eq = eq / eq.iloc[0]
    if len(trades):
        t = trades[(trades["entry_time"] >= pd.Timestamp(a, tz="UTC"))]
        if b:
            t = t[t["entry_time"] <= pd.Timestamp(b, tz="UTC")]
    else:
        t = trades
    return eq, metrics(eq, t, bpy)


def _init(interval, data_dir):
    global _DATA, _INTERVAL
    _INTERVAL = interval
    _DATA = load_basket(SYMBOLS, interval, data_dir)


def evaluate(args):
    """Run one (strategy, params) across the whole basket; return IS + OOS stats."""
    name, params = args
    fn = STRATEGIES[name]
    bpy = BARS_PER_YEAR[_INTERVAL]

    is_curves, oos_curves, rows = {}, {}, []
    for sym, df in _DATA.items():
        sig = fn(df, params, bpy)
        equity, trades = run(
            df, sig["entry"], sig["exit"], sig.get("stop_dist"), sig.get("size"),
            trail=sig.get("trail", True), max_bars=sig.get("max_bars"),
        )
        ise, ism = _window_stats(equity, trades, IS_START, IS_END, bpy)
        ose, osm = _window_stats(equity, trades, OOS_START, None, bpy)
        if ise is not None:
            is_curves[sym] = ise
        if ose is not None:
            oos_curves[sym] = ose
        rows.append({"symbol": sym,
                     "is": ism or {}, "oos": osm or {}})

    out = {"strategy": name, "params": params}
    for tag, curves, key in (("is", is_curves, "is"), ("oos", oos_curves, "oos")):
        if not curves:
            out[f"{tag}_sharpe"] = np.nan
            continue
        beq = basket_equity(curves)
        bm = metrics(beq, pd.DataFrame(columns=["ret", "bars"]), bpy)
        n_trades = int(sum(r[key].get("trades", 0) or 0 for r in rows))
        pos = [r[key] for r in rows if r[key].get("trades", 0) >= 3]
        out[f"{tag}_sharpe"] = bm["sharpe"]
        out[f"{tag}_cagr"] = bm["cagr"]
        out[f"{tag}_maxdd"] = bm["max_dd"]
        out[f"{tag}_sortino"] = bm["sortino"]
        out[f"{tag}_trades"] = n_trades
        out[f"{tag}_coins_ok"] = len(pos)
        out[f"{tag}_frac_coins_pos"] = (
            float(np.mean([p["total_return"] > 0 for p in pos])) if pos else np.nan)
        out[f"{tag}_median_coin_sharpe"] = (
            float(np.nanmedian([p["sharpe"] for p in pos])) if pos else np.nan)
    out["_rows"] = rows
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    os.makedirs(RESULTS, exist_ok=True)
    _init(args.interval, args.data_dir)
    print(f"Loaded {len(_DATA)} symbols @ {args.interval}")
    bpy = BARS_PER_YEAR[args.interval]

    # ---------------- benchmark: buy & hold ----------------
    bh = {}
    for tag, a, b in (("is", IS_START, IS_END), ("oos", OOS_START, None)):
        curves = {}
        for sym, df in _DATA.items():
            d = _slice(df["close"], a, b)
            if len(d) < 30:
                continue
            cost = (E.FEE_BPS + E.SLIP_BPS) / 10_000.0
            curves[sym] = d / d.iloc[0] * (1 - cost) ** 2
        beq = basket_equity(curves)
        m = metrics(beq, pd.DataFrame(columns=["ret", "bars"]), bpy)
        bh[tag] = m
        print(f"BUY&HOLD {tag.upper():3s}  CAGR {m['cagr']:+7.2%}  Sharpe {m['sharpe']:5.2f}  "
              f"MaxDD {m['max_dd']:7.2%}  ({len(curves)} coins)")

    # ---------------- sweep ----------------
    jobs = []
    for name in STRATEGIES:
        for p in grid(name):
            if valid(name, p):
                jobs.append((name, p))
    print(f"\nEvaluating {len(jobs)} (strategy, params) combos over {len(_DATA)} coins ...")

    results = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init,
                             initargs=(args.interval, args.data_dir)) as pool:
        for i, res in enumerate(pool.map(evaluate, jobs, chunksize=4), 1):
            results.append(res)
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)}")

    df = pd.DataFrame([{k: v for k, v in r.items() if k != "_rows"} for r in results])
    df["params_json"] = df["params"].apply(json.dumps, sort_keys=True)
    df = df.drop(columns=["params"])
    df.to_csv(os.path.join(RESULTS, f"sweep_all_{args.interval}.csv"), index=False)

    # ---------------- selection (IS only) ----------------
    elig = df[(df["is_trades"] >= MIN_TRADES_IS) & (df["is_coins_ok"] >= MIN_COINS)].copy()
    print(f"\n{len(elig)}/{len(df)} combos pass the minimum-activity filter")

    summary_rows = []
    per_family_winner = {}
    for name, g in elig.groupby("strategy"):
        g = g.sort_values("is_sharpe", ascending=False)
        top = g.head(10)
        best = g.iloc[0]
        per_family_winner[name] = json.loads(best["params_json"])
        print(f"\n=== {name} ===")
        print(f"  best IS params: {best['params_json']}")
        print(f"  IS : Sharpe {best['is_sharpe']:5.2f}  CAGR {best['is_cagr']:+7.2%}  "
              f"DD {best['is_maxdd']:7.2%}  trades {int(best['is_trades'])}")
        print(f"  OOS: Sharpe {best['oos_sharpe']:5.2f}  CAGR {best['oos_cagr']:+7.2%}  "
              f"DD {best['oos_maxdd']:7.2%}  trades {int(best['oos_trades'])}  "
              f"coins+ {best['oos_frac_coins_pos']:.0%}")
        print(f"  top-10 IS -> OOS Sharpe: median {top['oos_sharpe'].median():.2f}  "
              f"mean {top['oos_sharpe'].mean():.2f}  min {top['oos_sharpe'].min():.2f}  "
              f"max {top['oos_sharpe'].max():.2f}")
        summary_rows.append({
            "strategy": name, "params": best["params_json"],
            "is_sharpe": best["is_sharpe"], "is_cagr": best["is_cagr"],
            "is_maxdd": best["is_maxdd"], "is_trades": best["is_trades"],
            "oos_sharpe": best["oos_sharpe"], "oos_cagr": best["oos_cagr"],
            "oos_maxdd": best["oos_maxdd"], "oos_sortino": best["oos_sortino"],
            "oos_trades": best["oos_trades"],
            "oos_frac_coins_pos": best["oos_frac_coins_pos"],
            "oos_median_coin_sharpe": best["oos_median_coin_sharpe"],
            "top10_is_oos_sharpe_median": top["oos_sharpe"].median(),
            "top10_is_oos_sharpe_min": top["oos_sharpe"].min(),
        })

    summ = pd.DataFrame(summary_rows).sort_values("oos_sharpe", ascending=False)
    summ.to_csv(os.path.join(RESULTS, f"family_winners_{args.interval}.csv"), index=False)

    with open(os.path.join(RESULTS, f"benchmark_{args.interval}.json"), "w") as fh:
        json.dump({k: {kk: (None if pd.isna(vv) else float(vv)) for kk, vv in v.items()}
                   for k, v in bh.items()}, fh, indent=2)
    with open(os.path.join(RESULTS, f"selected_params_{args.interval}.json"), "w") as fh:
        json.dump(per_family_winner, fh, indent=2, sort_keys=True)

    print("\n" + summ.to_string(index=False))
    print(f"\nWrote results to {RESULTS}")


if __name__ == "__main__":
    main()
