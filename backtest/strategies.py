#!/usr/bin/env python3
"""
Candidate strategy families.

Every strategy is a function (df, params, bars_per_year) -> dict with keys
`entry`, `exit`, `stop_dist`, `size`, all pandas Series aligned to df.index and
computed ONLY from closed bars. The engine shifts them by one bar before acting,
so nothing here can peek at the bar it trades on.

`size` is the fraction of the coin's sleeve equity to deploy. When a strategy
uses volatility targeting, size = target_vol / realised_vol, capped at 1.0
(spot long-only: no leverage).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine import ema, sma, atr, rsi, realised_vol


def _size(df, p, bars_per_year):
    """Shared position sizing: 'full' or volatility-targeted."""
    if p.get("sizing", "full") == "full":
        return pd.Series(1.0, index=df.index)
    tv = p.get("target_vol", 0.60)
    rv = realised_vol(df["close"], p.get("vol_lookback", 30), bars_per_year)
    return (tv / rv).clip(upper=1.0).fillna(0.0)


_BTC_CACHE: dict[int, pd.Series] = {}


def set_btc(df: pd.DataFrame | None):
    """Register BTCUSDT daily data so strategies can use a market-wide filter."""
    global _BTC
    _BTC = df
    _BTC_CACHE.clear()


_BTC: pd.DataFrame | None = None


def btc_bull(n: int) -> pd.Series | None:
    """BTC close above its n-bar SMA — a single binary market-regime switch."""
    if _BTC is None or not n:
        return None
    if n not in _BTC_CACHE:
        _BTC_CACHE[n] = (_BTC["close"] > sma(_BTC["close"], n)).astype(float)
    return _BTC_CACHE[n]


def _regime(df, p):
    """Optional long-term regime filters: on the coin itself and/or on BTC."""
    ok = pd.Series(True, index=df.index)
    n = p.get("regime", 0)
    if n:
        ok &= df["close"] > sma(df["close"], n)
    bn = p.get("btc_regime", 0)
    if bn:
        b = btc_bull(bn)
        if b is not None:
            ok &= b.reindex(df.index).ffill().fillna(1.0) > 0.5
    return ok


# --------------------------------------------------------------------------- #
# 1. EMA trend following with ATR trailing stop
# --------------------------------------------------------------------------- #

def ema_atr(df, p, bpy):
    f = ema(df["close"], p["fast"])
    s = ema(df["close"], p["slow"])
    a = atr(df, p.get("atr_n", 14))
    up = f > s
    entry = up & ~up.shift(1).fillna(False) & _regime(df, p)
    exit_ = (~up) & up.shift(1).fillna(False)
    return {"entry": entry.fillna(False), "exit": exit_.fillna(False),
            "stop_dist": a * p.get("atr_k", 3.0), "size": _size(df, p, bpy)}


# --------------------------------------------------------------------------- #
# 2. Donchian channel breakout
# --------------------------------------------------------------------------- #

def donchian(df, p, bpy):
    # .shift(1) => the channel excludes the bar that breaks it
    hi = df["high"].rolling(p["n_in"], min_periods=p["n_in"]).max().shift(1)
    lo = df["low"].rolling(p["n_out"], min_periods=p["n_out"]).min().shift(1)
    a = atr(df, p.get("atr_n", 14))
    entry = (df["close"] > hi) & _regime(df, p)
    exit_ = df["close"] < lo
    return {"entry": entry.fillna(False), "exit": exit_.fillna(False),
            "stop_dist": a * p.get("atr_k", 3.0), "size": _size(df, p, bpy)}


# --------------------------------------------------------------------------- #
# 3. RSI / Bollinger mean reversion
# --------------------------------------------------------------------------- #

def rsi_bb(df, p, bpy):
    mid = sma(df["close"], p["bb_n"])
    sd = df["close"].rolling(p["bb_n"], min_periods=p["bb_n"]).std()
    lower = mid - p["bb_k"] * sd
    r = rsi(df["close"], p.get("rsi_n", 14))
    a = atr(df, p.get("atr_n", 14))
    entry = (df["close"] < lower) & (r < p["rsi_buy"]) & _regime(df, p)
    exit_ = (df["close"] > mid) | (r > p.get("rsi_sell", 60))
    return {"entry": entry.fillna(False), "exit": exit_.fillna(False),
            "stop_dist": a * p.get("atr_k", 3.0), "size": _size(df, p, bpy),
            "trail": False, "max_bars": p.get("max_bars", 20)}


# --------------------------------------------------------------------------- #
# 4. Volatility-adjusted time-series momentum
# --------------------------------------------------------------------------- #

def vol_momentum(df, p, bpy):
    roc = df["close"] / df["close"].shift(p["lookback"]) - 1.0
    rv = realised_vol(df["close"], p.get("vol_lookback", 30), bpy)
    score = roc / rv
    a = atr(df, p.get("atr_n", 14))
    long_ok = (score > p.get("thresh", 0.0)) & _regime(df, p)
    entry = long_ok & ~long_ok.shift(1).fillna(False)
    exit_ = (~long_ok) & long_ok.shift(1).fillna(False)
    return {"entry": entry.fillna(False), "exit": exit_.fillna(False),
            "stop_dist": a * p.get("atr_k", 4.0), "size": _size(df, p, bpy)}


# --------------------------------------------------------------------------- #
# 5. Volume-confirmed breakout  (matches the repo's VolScan theme)
# --------------------------------------------------------------------------- #

def vol_breakout(df, p, bpy):
    hi = df["high"].rolling(p["n_in"], min_periods=p["n_in"]).max().shift(1)
    vavg = df["quote_volume"].rolling(p["v_n"], min_periods=p["v_n"]).mean().shift(1)
    vsurge = df["quote_volume"] > p["v_mult"] * vavg
    a = atr(df, p.get("atr_n", 14))
    entry = (df["close"] > hi) & vsurge & _regime(df, p)
    lo = df["low"].rolling(p["n_out"], min_periods=p["n_out"]).min().shift(1)
    exit_ = df["close"] < lo
    return {"entry": entry.fillna(False), "exit": exit_.fillna(False),
            "stop_dist": a * p.get("atr_k", 3.0), "size": _size(df, p, bpy)}


STRATEGIES = {
    "ema_atr": ema_atr,
    "donchian": donchian,
    "rsi_bb": rsi_bb,
    "vol_momentum": vol_momentum,
    "vol_breakout": vol_breakout,
}


# --------------------------------------------------------------------------- #
# parameter grids (in-sample tuning space)
# --------------------------------------------------------------------------- #

def grid(name):
    import itertools

    def expand(d):
        keys = list(d)
        return [dict(zip(keys, v)) for v in itertools.product(*(d[k] for k in keys))]

    if name == "ema_atr":
        return expand({"fast": [10, 20, 30, 50], "slow": [50, 100, 150, 200],
                       "atr_k": [2.0, 3.0, 4.0], "regime": [0, 200], "btc_regime": [0, 200],
                       "sizing": ["full", "voltarget"]})
    if name == "donchian":
        return expand({"n_in": [20, 40, 55, 80], "n_out": [10, 20, 40],
                       "atr_k": [2.0, 3.0, 4.0], "regime": [0, 200], "btc_regime": [0, 200],
                       "sizing": ["full", "voltarget"]})
    if name == "rsi_bb":
        return expand({"bb_n": [20, 30], "bb_k": [2.0, 2.5, 3.0],
                       "rsi_buy": [25, 30, 35], "atr_k": [2.0, 3.0],
                       "max_bars": [10, 20], "regime": [0, 200], "btc_regime": [0, 200],
                       "sizing": ["full", "voltarget"]})
    if name == "vol_momentum":
        return expand({"lookback": [30, 60, 90, 120], "thresh": [0.0, 0.25, 0.5],
                       "atr_k": [3.0, 4.0, 6.0], "regime": [0, 200], "btc_regime": [0, 200],
                       "sizing": ["full", "voltarget"]})
    if name == "vol_breakout":
        return expand({"n_in": [20, 40, 55], "n_out": [10, 20, 40],
                       "v_mult": [1.5, 2.0, 3.0], "v_n": [20, 30],
                       "atr_k": [2.0, 3.0], "regime": [0, 200], "btc_regime": [0, 200],
                       "sizing": ["full", "voltarget"]})
    raise KeyError(name)


def valid(name, p):
    if name in ("ema_atr",) and p["fast"] >= p["slow"]:
        return False
    if name in ("donchian", "vol_breakout") and p["n_out"] > p["n_in"]:
        return False
    return True
