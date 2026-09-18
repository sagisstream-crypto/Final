#!/usr/bin/env python3
"""
Final report for the recommended configuration.

Everything here is a presentation of results already produced by
`walkforward.py`; nothing new is selected on out-of-sample data. It prints and
saves:

  * year-by-year returns of the recommended fixed configuration vs buy & hold;
  * the same for the annually re-tuned walk-forward protocol;
  * a sensitivity table around the recommended parameters (does the result
    survive when every knob is moved?);
  * a 50/50 blend with the best per-coin trend family, for comparison;
  * turnover and how much of the whole result comes from the 2021 bull year.

Usage:
    python3 backtest/final_strategy.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine as E
import walkforward as W
from engine import metrics, basket_equity, BARS_PER_YEAR
from xsec import run_xsec
from strategies import STRATEGIES

RESULTS = W.RESULTS
BPY = 365

# Recommended configuration. The parameter VALUES are not the grid's
# out-of-sample maximum — they are the middle of the region that the grid shows
# to be insensitive (k and lookback barely matter; rebalance must be <= 14 days;
# the BTC regime filter is the one setting that matters a lot).
RECOMMENDED = {"lookback": 60, "k": 5, "rebalance": 14, "thresh": 0.0,
               "btc_regime": 200, "inv_vol": True}


def yearly(eq: pd.Series) -> pd.Series:
    return eq.resample("YE").last().pct_change().fillna(
        eq.resample("YE").last().iloc[0] / eq.iloc[0] - 1.0)


def stat_line(eq, label):
    m = metrics(eq, pd.DataFrame(columns=["ret", "bars"]), BPY)
    return {"config": label, "cagr": m["cagr"], "sharpe": m["sharpe"],
            "sortino": m["sortino"], "max_dd": m["max_dd"],
            "calmar": m["calmar"], "total_x": eq.iloc[-1] / eq.iloc[0]}


def main():
    W._init("1d", None)
    data, btc = W._DATA, W._BTC
    print(f"{len(data)} coins, {min(d.index[0] for d in data.values()).date()}"
          f" .. {max(d.index[-1] for d in data.values()).date()}\n")

    # ---------- benchmark ----------
    cost = (E.FEE_BPS + E.SLIP_BPS) / 1e4
    bh = {}
    for sym, df in data.items():
        c = W._win(df["close"], "2021-01-01", "2026-12-31")
        if len(c) < 60:
            continue
        bh[sym] = c / c.iloc[0] * (1 - cost) ** 2
    bh_eq = basket_equity(bh)

    # ---------- recommended fixed config ----------
    eq, w, turn = run_xsec(data, RECOMMENDED, BPY, btc)
    eq = W._win(eq, "2021-01-01", "2026-12-31")
    eq = eq / eq.iloc[0]

    # ---------- trend comparison (best per-coin family) ----------
    vm_params = W.FIXED["vol_momentum"]
    curves = {}
    for sym, df in data.items():
        sig = STRATEGIES["vol_momentum"](df, vm_params, BPY)
        e, _ = E.run(df, sig["entry"], sig["exit"], sig.get("stop_dist"), sig.get("size"))
        curves[sym] = e
    vm_eq = W._win(basket_equity(curves), "2021-01-01", "2026-12-31")
    vm_eq = vm_eq / vm_eq.iloc[0]

    # ---------- 50/50 blend ----------
    blend_r = pd.concat([eq.pct_change(), vm_eq.pct_change()], axis=1).mean(axis=1).fillna(0)
    blend = (1 + blend_r).cumprod()

    def ex21(e):
        e = e[e.index >= "2022-01-01"]
        return e / e.iloc[0]

    rows = []
    for e, label in [(eq, "xsec_mom RECOMMENDED (fixed)"),
                     (vm_eq, "vol_momentum (fixed)"),
                     (blend, "50/50 blend"),
                     (bh_eq, "BUY & HOLD (equal weight)")]:
        r = stat_line(e, label)
        r2 = stat_line(ex21(e), label)
        r.update({"cagr_ex2021": r2["cagr"], "sharpe_ex2021": r2["sharpe"],
                  "maxdd_ex2021": r2["max_dd"], "total_x_ex2021": r2["total_x"]})
        rows.append(r)
    summ = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    print("=== 2021-01 .. 2026-08, net of 0.175% per side ===")
    print(summ.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # ---------- year by year ----------
    yr = pd.DataFrame({
        "xsec_recommended": yearly(eq),
        "vol_momentum": yearly(vm_eq),
        "blend": yearly(blend),
        "buy_hold": yearly(bh_eq),
    })
    yr.index = yr.index.year
    print("\n=== YEAR BY YEAR (2026 is partial, to 31 Aug) ===")
    print((yr * 100).to_string(float_format=lambda x: f"{x:,.1f}%"))

    # ---------- how much is just 2021? ----------
    ex21 = eq[eq.index >= "2022-01-01"]
    ex21 = ex21 / ex21.iloc[0]
    b21 = bh_eq[bh_eq.index >= "2022-01-01"]
    b21 = b21 / b21.iloc[0]
    print("\n=== EXCLUDING THE 2021 BULL YEAR (2022-01 .. 2026-08) ===")
    print(pd.DataFrame([stat_line(ex21, "xsec_mom RECOMMENDED"),
                        stat_line(b21, "BUY & HOLD")])
          .to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # ---------- sensitivity ----------
    sens = []
    for key, values in [("lookback", [30, 60, 90, 120]), ("k", [3, 5, 8]),
                        ("rebalance", [7, 14, 30]), ("thresh", [0.0, 0.5]),
                        ("btc_regime", [0, 100, 150, 200, 250]), ("inv_vol", [False, True])]:
        for v in values:
            p = dict(RECOMMENDED, **{key: v})
            e2, _, _ = run_xsec(data, p, BPY, btc)
            e2 = W._win(e2, "2021-01-01", "2026-12-31")
            e2 = e2 / e2.iloc[0]
            m = metrics(e2, pd.DataFrame(columns=["ret", "bars"]), BPY)
            e3 = e2[e2.index >= "2022-01-01"]
            m3 = metrics(e3 / e3.iloc[0], pd.DataFrame(columns=["ret", "bars"]), BPY)
            sens.append({"knob": key, "value": v, "cagr": m["cagr"],
                         "sharpe": m["sharpe"], "max_dd": m["max_dd"],
                         "cagr_ex2021": m3["cagr"], "sharpe_ex2021": m3["sharpe"]})
    sens = pd.DataFrame(sens)
    print("\n=== SENSITIVITY: move one knob at a time off the recommendation ===")
    print(sens.to_string(index=False, float_format=lambda x: f"{x:,.3f}"))

    # ---------- turnover ----------
    t = W._win(turn, "2021-01-01", "2026-12-31")
    print(f"\nTurnover: {t.sum() / (len(t) / 365):.1f}x of capital per year "
          f"(~{t.sum() / (len(t) / 365) * cost * 100:.2f}% per year in costs)")
    inv = (w.abs().sum(axis=1))
    inv = W._win(inv, "2021-01-01", "2026-12-31")
    print(f"Average invested: {inv.mean():.0%} of capital; "
          f"fully in cash on {float((inv < 0.01).mean()):.0%} of days")

    summ.to_csv(os.path.join(RESULTS, "final_comparison.csv"), index=False)
    yr.to_csv(os.path.join(RESULTS, "final_yearly.csv"))
    sens.to_csv(os.path.join(RESULTS, "final_sensitivity.csv"), index=False)
    pd.DataFrame({"xsec_recommended": eq, "vol_momentum": vm_eq,
                  "blend": blend, "buy_hold": bh_eq}).resample("W").last().to_csv(
        os.path.join(RESULTS, "final_curves_weekly.csv"))
    with open(os.path.join(RESULTS, "final_params.json"), "w") as fh:
        json.dump({"recommended_xsec_mom": RECOMMENDED,
                   "vol_momentum_fixed": vm_params,
                   "fee_bps": E.FEE_BPS, "slippage_bps": E.SLIP_BPS,
                   "universe": sorted(data)}, fh, indent=2)
    print(f"\nWrote results to {RESULTS}")


if __name__ == "__main__":
    main()
