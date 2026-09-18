#!/usr/bin/env python3
"""
Cross-sectional momentum rotation — a portfolio-level family.

Unlike the per-coin sleeve strategies, this one ranks the whole basket against
itself and holds only the top K names. It therefore needs its own runner.

Rules
-----
* On the close of a rebalance bar, score every coin that has `lookback` bars of
  history:  score = (close / close[-lookback] - 1) / realised_vol.
* Keep the top K scores that are also above `thresh`.
* If a market filter is on and BTC is below its SMA, go to cash instead.
* Weights are equal across held names (optionally inverse-vol). Unused weight
  stays in cash (0% return — no lending yield assumed).
* The new portfolio is executed at the NEXT bar's open; costs are charged on the
  turnover (sum of absolute weight changes) at fee + slippage per side.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine import realised_vol, sma, FEE_BPS, SLIP_BPS


def run_xsec(data: dict[str, pd.DataFrame], p: dict, bpy: int,
             btc: pd.DataFrame | None = None,
             fee_bps: float = FEE_BPS, slip_bps: float = SLIP_BPS):
    """Return (equity Series, weights DataFrame, turnover Series)."""
    syms = sorted(data)
    close = pd.DataFrame({s: data[s]["close"] for s in syms}).sort_index()
    open_ = pd.DataFrame({s: data[s]["open"] for s in syms}).reindex(close.index)

    lb = p["lookback"]
    mom = close / close.shift(lb) - 1.0
    rv = pd.DataFrame({s: realised_vol(close[s], p.get("vol_lookback", 30), bpy)
                       for s in syms})
    score = mom / rv

    # eligibility: enough history AND a real price series at that bar
    live = close.notna() & close.shift(lb).notna() & rv.notna() & (rv > 0)
    score = score.where(live)

    bull = pd.Series(True, index=close.index)
    if p.get("btc_regime") and btc is not None:
        b = (btc["close"] > sma(btc["close"], p["btc_regime"]))
        bull = b.reindex(close.index).ffill().fillna(False)

    rb = p.get("rebalance", 7)
    k = p["k"]
    thresh = p.get("thresh", 0.0)
    inv_vol = p.get("inv_vol", False)

    # ---- target weights, decided at each rebalance bar's CLOSE ----
    tgt = pd.DataFrame(0.0, index=close.index, columns=syms)
    bar_no = np.arange(len(close))
    rebal_bars = set(bar_no[bar_no % rb == 0])
    cur = pd.Series(0.0, index=syms)
    for i, ts in enumerate(close.index):
        if i in rebal_bars:
            if not bull.iloc[i]:
                cur = pd.Series(0.0, index=syms)
            else:
                row = score.iloc[i].dropna()
                row = row[row > thresh]
                pick = row.nlargest(k).index
                cur = pd.Series(0.0, index=syms)
                if len(pick):
                    if inv_vol:
                        w = 1.0 / rv.iloc[i][pick]
                        w = w / w.sum() * (len(pick) / k)
                    else:
                        w = pd.Series(1.0 / k, index=pick)
                    cur[pick] = w.values
        tgt.iloc[i] = cur.values

    # ---- execute at next bar's open; hold through that bar ----
    w = tgt.shift(1).fillna(0.0)
    # bar return earned by a position held from this bar's open to next open
    nxt_open = open_.shift(-1)
    bar_ret = (nxt_open / open_ - 1.0).fillna(0.0)
    bar_ret = bar_ret.where(np.isfinite(bar_ret), 0.0)

    cost = (fee_bps + slip_bps) / 10_000.0
    turnover = (w - w.shift(1).fillna(0.0)).abs().sum(axis=1)

    gross = (w * bar_ret).sum(axis=1)
    net = gross - turnover * cost
    equity = (1.0 + net).cumprod()
    return equity, w, turnover


def xsec_grid():
    import itertools
    d = {"lookback": [30, 60, 90, 120], "k": [3, 5, 8], "rebalance": [7, 14, 30],
         "thresh": [0.0, 0.5], "btc_regime": [0, 200], "inv_vol": [False, True]}
    keys = list(d)
    return [dict(zip(keys, v)) for v in itertools.product(*(d[k] for k in keys))]
