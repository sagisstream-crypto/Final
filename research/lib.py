"""Shared backtest harness for 'вынос' (liquidity sweep / spike) research.
Data: data/{spot|fut}_{SYMBOL}.parquet, 5m klines 2026-03..2026-08, alts only (no BTC/ETH/majors/stables).
Columns: ot(ms) o h l c v qv(quote vol) n(trades) tbqv(taker buy quote vol).
Rules: signal known at CLOSE of bar i -> entry at OPEN of bar i+1. TP/SL checked on bars i+1..i+hold.
If TP and SL both touched in the same bar -> counted as SL (conservative). Timeout -> exit at close of last bar.
Fee: FEE round-trip (0.1%) subtracted from every trade's return.
Split: TRAIN = ot < 2026-07-01, TEST = ot >= 2026-07-01 (out-of-sample, never tune on it).
"""
import glob, os, numpy as np, pandas as pd
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SPLIT = pd.Timestamp("2026-07-01", tz="UTC").value // 10**6
FEE = 0.001
_cache = {}
def symbols(market=None):
    fs = sorted(glob.glob(f"{D}/*.parquet"))
    out = [os.path.basename(f)[:-8].split("_", 1) for f in fs]
    return [(m, s) for m, s in out if market is None or m == market]
def load(market, sym):
    k = (market, sym)
    if k not in _cache: _cache[k] = pd.read_parquet(f"{D}/{market}_{sym}.parquet")
    return _cache[k]
def simulate(df, sig, side, tp, sl, hold):
    """sig: bool array (signal at close of bar i). side: +1 long / -1 short (scalar or array).
    tp, sl: fractional distances (scalar or per-bar arrays, >0). Returns DataFrame of trades.
    Non-overlapping: after a trade opens, new signals on the same symbol are ignored until it exits."""
    o, h, l, c, t = (df[x].values for x in ("o", "h", "l", "c", "ot"))
    n = len(df); idx = np.flatnonzero(sig); res = []; busy = -1
    side = np.broadcast_to(np.asarray(side), (n,)); tp = np.broadcast_to(np.asarray(tp, float), (n,)); sl = np.broadcast_to(np.asarray(sl, float), (n,))
    for i in idx:
        if i <= busy or i + 1 >= n: continue
        e = o[i + 1]; s = side[i]; tpp = e * (1 + s * tp[i]); slp = e * (1 - s * sl[i])
        end = min(n - 1, i + hold); r = None; j = end
        for j in range(i + 1, end + 1):
            hit_sl = l[j] <= slp if s > 0 else h[j] >= slp
            hit_tp = h[j] >= tpp if s > 0 else l[j] <= tpp
            if hit_sl: r = -sl[i]; break
            if hit_tp: r = tp[i]; break
        if r is None: r = s * (c[end] / e - 1); j = end
        busy = j
        res.append((t[i], i, s, r - FEE, r > 0))
    return pd.DataFrame(res, columns=["ot", "i", "side", "ret", "win"])
def report(trades, label=""):
    out = {}
    for name, m in (("train", trades.ot < SPLIT), ("test", trades.ot >= SPLIT)):
        tr = trades[m]
        if len(tr) == 0: out[name] = "n=0"; continue
        win = (tr.ret > 0).mean(); ev = tr.ret.mean()
        out[name] = f"n={len(tr)} win={win:.1%} ev={ev*100:+.3f}% sum={tr.ret.sum()*100:+.1f}%"
    print(label, "|", out["train"], "||", out["test"], flush=True)
    return out
def run_all(signal_fn, tp, sl, hold, markets=("spot", "fut"), label=""):
    """signal_fn(df) -> (sig bool array, side array/scalar[, tp, sl arrays]). Runs over every symbol."""
    allt = []
    for m, s in symbols():
        if m not in markets: continue
        df = load(m, s); r = signal_fn(df)
        sig, side = r[0], r[1]
        _tp = r[2] if len(r) > 2 else tp; _sl = r[3] if len(r) > 3 else sl
        t = simulate(df, sig, side, _tp, _sl, hold); t["sym"] = s; t["mkt"] = m; allt.append(t)
    trades = pd.concat(allt, ignore_index=True) if allt else pd.DataFrame(columns=["ot","ret","win"])
    report(trades, label); return trades
