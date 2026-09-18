#!/usr/bin/env python3
"""
Vectorised-indicator / explicit-state-machine backtest engine.

Design rules (all deliberate, all documented):

* LONG-ONLY SPOT. The repo is a Binance spot scanner, so no shorts, no leverage.
  Position size is a fraction (<= 1.0) of that coin's sleeve equity.

* NO LOOKAHEAD. Indicators are computed from closed bars only. A signal that
  becomes true at the close of bar t is executed at the OPEN of bar t+1.
  Every indicator is shifted by one bar before the state machine sees it.

* INTRABAR STOPS. Stops are checked against the bar's low (long stop). If the
  bar gaps through the stop (open <= stop) we fill at the open, otherwise at the
  stop price. This is the conservative reading, not the optimistic one.

* REAL COSTS. `fee_bps` (Binance spot taker = 10 bps) plus `slip_bps`
  (default 7.5 bps) are charged on EVERY entry and EVERY exit.
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

FEE_BPS = 10.0      # Binance spot taker fee, one side
SLIP_BPS = 7.5      # conservative slippage assumption, one side
BARS_PER_YEAR = {"1d": 365, "4h": 365 * 6, "1h": 365 * 24}


# --------------------------------------------------------------------------- #
# data loading
# --------------------------------------------------------------------------- #

MAX_BAR_GAP = 5.0   # open/prev_close beyond this is a data break, not a return


def load(symbol: str, interval: str = "1d", data_dir: str | None = None) -> pd.DataFrame:
    path = os.path.join(data_dir or os.path.join(DATA_DIR, interval), f"{symbol}-{interval}.csv")
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    keep = ["open", "high", "low", "close", "volume", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote"]
    for c in keep:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df["close"] > 0]

    # --- ticker reuse / redenomination guard -------------------------------
    # A continuously traded pair cannot gap 5x between one bar's close and the
    # next bar's open. When it does, the ticker has been reassigned to a
    # different asset. The real case in this dataset is LUNAUSDT: after the
    # Terra collapse the symbol was reused for Terra 2.0 on 2022-05-31, a
    # 20000x one-bar "return" that would otherwise dominate the whole basket.
    # Everything from the break onward refers to a different asset, so it is
    # dropped; the pre-break history — including the collapse itself — is kept,
    # which is exactly the part that matters for honest backtesting.
    gap = df["open"] / df["close"].shift(1)
    brk = gap[(gap > MAX_BAR_GAP) | (gap < 1.0 / MAX_BAR_GAP)]
    if len(brk):
        df = df.loc[: brk.index[0]].iloc[:-1]

    return df[keep]


def load_basket(symbols, interval="1d", data_dir=None) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        try:
            out[s] = load(s, interval, data_dir)
        except FileNotFoundError:
            pass
    return out


# --------------------------------------------------------------------------- #
# indicators
# --------------------------------------------------------------------------- #

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def realised_vol(s: pd.Series, n: int, bars_per_year: int) -> pd.Series:
    return s.pct_change().rolling(n, min_periods=n).std() * np.sqrt(bars_per_year)


# --------------------------------------------------------------------------- #
# state machine
# --------------------------------------------------------------------------- #

def run(df: pd.DataFrame,
        entry: pd.Series,
        exit_: pd.Series,
        stop_dist: pd.Series | None = None,
        size: pd.Series | None = None,
        fee_bps: float = FEE_BPS,
        slip_bps: float = SLIP_BPS,
        trail: bool = True,
        max_bars: int | None = None):
    """
    Simulate one coin's sleeve.

    entry / exit_ : bool Series, TRUE AT THE CLOSE OF BAR t -> acted on at
                    the open of bar t+1 (shifted internally).
    stop_dist     : absolute price distance for the protective stop, measured
                    from the entry price (and trailed off the running high when
                    `trail` is True). Known at the signal bar's close.
    size          : fraction of sleeve equity to deploy, in (0, 1].

    Returns (equity Series indexed like df, trades DataFrame).
    """
    n = len(df)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)

    # shift(1): decision made on bar t-1's close acts on bar t's open
    en = entry.shift(1).fillna(False).to_numpy(bool)
    ex = exit_.shift(1).fillna(False).to_numpy(bool)
    sd = (stop_dist.shift(1).to_numpy(float) if stop_dist is not None
          else np.full(n, np.nan))
    sz = (size.shift(1).fillna(0.0).to_numpy(float) if size is not None
          else np.ones(n))
    sz = np.clip(np.nan_to_num(sz, nan=0.0), 0.0, 1.0)

    cost = (fee_bps + slip_bps) / 10_000.0

    equity = np.ones(n)
    eq = 1.0
    in_pos = False
    entry_px = 0.0
    frac = 0.0            # fraction of sleeve currently deployed
    stop = np.nan
    run_high = 0.0
    bars_held = 0
    entry_i = 0
    trades = []

    def close_at(price, i, reason):
        nonlocal eq, in_pos, frac, bars_held
        fill = price * (1 - cost)
        gross = fill / entry_px - 1.0
        eq *= 1.0 + frac * gross
        trades.append({
            "entry_time": df.index[entry_i], "exit_time": df.index[i],
            "entry_px": entry_px, "exit_px": fill, "bars": bars_held,
            "ret": gross, "weighted_ret": frac * gross,
            "size": frac, "reason": reason,
        })
        in_pos = False
        frac = 0.0
        bars_held = 0

    for i in range(n):
        if in_pos:
            bars_held += 1
            # 1) protective stop first (worst case inside the bar)
            if not np.isnan(stop):
                if o[i] <= stop:
                    close_at(o[i], i, "stop_gap")
                elif l[i] <= stop:
                    close_at(stop, i, "stop")
            # 2) signal / time exit at this bar's open
            if in_pos and ex[i]:
                close_at(o[i], i, "signal")
            if in_pos and max_bars is not None and bars_held >= max_bars:
                close_at(c[i], i, "time")
            # 3) trail the stop off the running high of closed bars
            if in_pos:
                run_high = max(run_high, h[i])
                if trail and not np.isnan(sd[i]):
                    stop = max(stop, run_high - sd[i]) if not np.isnan(stop) else run_high - sd[i]
                eq_mark = eq * (1.0 + frac * (c[i] / entry_px - 1.0))
            else:
                eq_mark = eq
        else:
            if en[i] and sz[i] > 0:
                entry_px = o[i] * (1 + cost)
                in_pos = True
                frac = sz[i]
                entry_i = i
                bars_held = 0
                run_high = h[i]
                stop = (o[i] - sd[i]) if not np.isnan(sd[i]) else np.nan
                if trail and not np.isnan(sd[i]):
                    stop = max(stop, run_high - sd[i])
                eq_mark = eq * (1.0 + frac * (c[i] / entry_px - 1.0))
            else:
                eq_mark = eq
        equity[i] = eq_mark

    if in_pos:
        close_at(c[n - 1], n - 1, "open_at_end")
        equity[n - 1] = eq

    return (pd.Series(equity, index=df.index),
            pd.DataFrame(trades, columns=["entry_time", "exit_time", "entry_px", "exit_px",
                                          "bars", "ret", "weighted_ret", "size", "reason"]))


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #

def metrics(equity: pd.Series, trades: pd.DataFrame, bars_per_year: int) -> dict:
    eq = equity.dropna()
    if len(eq) < 2:
        return {k: np.nan for k in ("cagr", "sharpe", "sortino", "max_dd", "calmar",
                                    "win_rate", "profit_factor", "avg_trade", "trades",
                                    "exposure", "total_return", "years")}
    r = eq.pct_change().fillna(0.0)
    years = len(eq) / bars_per_year
    total = eq.iloc[-1] / eq.iloc[0]
    cagr = total ** (1 / years) - 1 if years > 0 and total > 0 else np.nan
    sd = r.std()
    sharpe = (r.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else np.nan
    dn = r[r < 0].std()
    sortino = (r.mean() / dn * np.sqrt(bars_per_year)) if dn and dn > 0 else np.nan
    dd = (eq / eq.cummax() - 1.0).min()

    if len(trades):
        w = trades["ret"] > 0
        gp = trades.loc[w, "ret"].sum()
        gl = -trades.loc[~w, "ret"].sum()
        pf = gp / gl if gl > 0 else np.inf
        win = w.mean()
        avg = trades["ret"].mean()
        exposure = trades["bars"].sum() / len(eq)
    else:
        pf = win = avg = np.nan
        exposure = 0.0

    return {
        "cagr": cagr, "sharpe": sharpe, "sortino": sortino, "max_dd": dd,
        "calmar": (cagr / abs(dd)) if dd and dd < 0 and not np.isnan(cagr) else np.nan,
        "win_rate": win, "profit_factor": pf, "avg_trade": avg,
        "trades": len(trades), "exposure": exposure,
        "total_return": total - 1.0, "years": years,
    }


def buy_hold(df: pd.DataFrame, bars_per_year: int) -> dict:
    cost = (FEE_BPS + SLIP_BPS) / 10_000.0
    eq = df["close"] / df["close"].iloc[0] * (1 - cost) ** 2
    m = metrics(eq, pd.DataFrame(columns=["ret", "bars"]), bars_per_year)
    m["trades"] = 1
    m["exposure"] = 1.0
    return m


def ew_portfolio(arr: np.ndarray, period_id: np.ndarray) -> np.ndarray:
    """
    Equal-weight portfolio of per-coin return streams, rebalanced to 1/N at the
    start of each period and left to drift in between.

    The rebalancing convention is not cosmetic: rebalancing daily quietly adds a
    return nobody could harvest (you do not move capital across fifty sleeves
    every day), and never rebalancing lets one winner become the whole basket.
    Monthly is what a person would actually do, and the SAME convention is used
    for the strategies and for the buy & hold benchmark so the comparison is
    like for like.

    arr       : (T, N) simple returns, NaN where the coin is not live.
    period_id : (T,) integer period label (e.g. year*12+month).
    """
    T = arr.shape[0]
    out = np.zeros(T)
    if T == 0:
        return out
    finite = np.isfinite(arr)
    r = np.where(finite, arr, 0.0)
    cuts = np.flatnonzero(np.diff(period_id)) + 1
    bounds = np.concatenate(([0], cuts, [T]))
    for a, b in zip(bounds[:-1], bounds[1:]):
        live = finite[a:b].any(axis=0)
        if not live.any():
            continue
        cum = np.cumprod(1.0 + r[a:b][:, live], axis=0)
        pv = cum.mean(axis=1)                       # equal weights at the reset
        prev = np.concatenate(([1.0], pv[:-1]))
        out[a:b] = pv / prev - 1.0
    return out


def period_id(index: pd.DatetimeIndex) -> np.ndarray:
    return (index.year * 12 + index.month).to_numpy()


def basket_equity(curves: dict[str, pd.Series]) -> pd.Series:
    """Monthly-rebalanced equal-weight portfolio of per-coin sleeve curves."""
    rets = pd.DataFrame({k: v.pct_change() for k, v in curves.items()})
    port = ew_portfolio(rets.to_numpy(float), period_id(rets.index))
    return pd.Series(np.cumprod(1.0 + port), index=rets.index)
