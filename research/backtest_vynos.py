#!/usr/bin/env python3
"""Standalone backtest of the «Вынос» strategy (4h-low sweep + confirmed bounce, LONG only)
on Binance alt pairs, spot + USDT-M futures, with a portfolio simulation from a starting balance.

    pip install pandas numpy pyarrow
    python backtest_vynos.py                         # Mar–Sep 2026, $10k, 10% per trade, max 10 positions
    python backtest_vynos.py --start 2026-07-01 --capital 5000 --size 0.2 --max-pos 5 --slip 0.001
    python backtest_vynos.py --markets fut --out my_run

Data comes from the public archive data.binance.vision (monthly files, daily files for the
current month) and is cached in ./vynos_data. No API key needed.

Signal (5m klines), known at the CLOSE of confirmation bar k = i+1:
  sweep bar i : low[i] < min(low[i-48..i-1]) and close[i] > that min      (4h low swept, closed back)
                (close[i]/close[i-12]-1) / ATR% > -2                     (no dump before the sweep)
                ATR% = mean true range of bars i-48..i-1 / close[i]
  bar k       : w = close[k]/low[i] - 1 in [1.5%, 10%), UTC hour of bar k >= 12
  symbol      : at least 7 days of history in the loaded data
Trade: entry at open of bar k+1, TP = +0.5*w, SL = -2*w, time exit at close after 48 bars (4h).
TP and SL touched in the same bar -> counted as SL. Fee 0.1% round trip (+ optional --slip).
One open trade per symbol+market at a time.

Portfolio: each trade gets notional = size * current equity (default 10%), 1x leverage.
If --max-pos positions are already open, the signal is skipped. PnL is realised at exit.
"""
import argparse, concurrent.futures as cf, io, json, os, re, sys, urllib.request, zipfile
import numpy as np, pandas as pd

# ---------------- parameters of the strategy ----------------
N, ATR_N, RET12_MIN = 48, 48, -2.0
W_LO, W_HI, H_FROM = 0.015, 0.10, 12
TPK, SLK, HOLD, MIN_BARS = 0.5, 2.0, 48, 2016
FEE = 0.001

MAJORS = set("BTC ETH BNB SOL XRP DOGE ADA TRX TON LINK AVAX LTC BCH DOT XLM SHIB HBAR SUI UNI NEAR APT ICP ETC FIL ATOM".split())
STABLE = set("USDC FDUSD TUSD USD1 RLUSD USDP DAI EUR XAUT PAXG U USDE BFUSD AEUR".split())
COLS = "ot o h l c v ct qv n tbv tbqv ig".split()


# ---------------- data ----------------
def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()

def ok_sym(s):
    if not s.endswith("USDT"):
        return False
    b = s[:-4]
    return (b.isascii() and b.isalnum() and b not in MAJORS and b not in STABLE
            and not b.endswith(("UP", "DOWN", "BULL", "BEAR")) and not s.endswith("BUSDT"))

def pick_universe(n_spot, n_fut, min_qv):
    """Current top alt pairs by 24h spot volume. NB: today's list → survivorship bias for the past."""
    t = json.loads(get("https://data-api.binance.vision/api/v3/ticker/24hr"))
    spot = sorted((x for x in t if ok_sym(x["symbol"]) and float(x["quoteVolume"]) > min_qv),
                  key=lambda x: -float(x["quoteVolume"]))
    spot = [x["symbol"] for x in spot][:n_spot]
    lst = get("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/futures/um/monthly/klines/&delimiter=/").decode()
    fut_all = set(re.findall(r"klines/([A-Z0-9]+)/", lst))
    fut = [s for s in spot if s in fut_all][:n_fut]
    return [("spot", s) for s in spot] + [("fut", s) for s in fut]

def _read_zip(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    df = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(df.iloc[0, 0]).isdigit():
        df = df.iloc[1:]
    df.columns = COLS
    return df

def load_symbol(mkt, sym, start, end, cache):
    base = "futures/um" if mkt == "fut" else "spot"
    parts = []
    for m in pd.period_range(start - pd.Timedelta(days=8), end, freq="M"):  # +8 days warm-up
        fn = f"{cache}/{mkt}_{sym}_{m}.parquet"
        if os.path.exists(fn):
            parts.append(pd.read_parquet(fn)); continue
        month_done = m.end_time.tz_localize("UTC") < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=3)
        try:
            df = _read_zip(get(f"https://data.binance.vision/data/{base}/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip"))
        except Exception:
            days = []
            for d in pd.date_range(m.start_time, m.end_time, freq="D"):
                if d.tz_localize("UTC") > pd.Timestamp.now(tz="UTC"):
                    break
                dfn = f"{cache}/{mkt}_{sym}_{d:%Y-%m-%d}.parquet"
                if os.path.exists(dfn):
                    days.append(pd.read_parquet(dfn)); continue
                try:
                    day = _read_zip(get(f"https://data.binance.vision/data/{base}/daily/klines/{sym}/5m/{sym}-5m-{d:%Y-%m-%d}.zip"))
                    day[["ot", "o", "h", "l", "c", "qv"]].astype(float).to_parquet(dfn)
                    days.append(day)
                except Exception:
                    pass
            if not days:
                continue
            df = pd.concat(days); month_done = False
        df = df.astype(float)
        df["ot"] = df["ot"].where(df["ot"] < 1e14, df["ot"] // 1000)  # spot archive switched to µs in 2025
        df = df[["ot", "o", "h", "l", "c", "qv"]]
        if month_done:
            df.to_parquet(fn)
        parts.append(df)
    if not parts:
        return None
    return pd.concat(parts).drop_duplicates("ot").sort_values("ot").reset_index(drop=True)


# ---------------- signal + trade simulation (identical to vynos-core.js) ----------------
def signals(df):
    h, l, c, ot = (df[x].values for x in ("h", "l", "c", "ot"))
    S = pd.Series
    pc = S(c).shift(1).values
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    atrp = S(tr).rolling(ATR_N).mean().shift(1).values / c
    lowN = S(l).shift(1).rolling(N).min().values
    with np.errstate(all="ignore"):
        ret12 = (c / S(c).shift(12).values - 1) / atrp
        sweep = (l < lowN) & (c > lowN) & (ret12 > RET12_MIN)
        sw_prev = np.r_[False, sweep[:-1]]
        w = c / np.r_[np.nan, l[:-1]] - 1
        sig = sw_prev & (w >= W_LO) & (w < W_HI)
    sig = np.nan_to_num(sig).astype(bool)
    sig[:MIN_BARS] = False
    sig &= (ot // 3600000) % 24 >= H_FROM
    return sig, w

def trades_for(df, mkt, sym, slip):
    sig, w = signals(df)
    o, h, l, c, ot = (df[x].values for x in ("o", "h", "l", "c", "ot"))
    n, busy, out = len(df), -1, []
    for k in np.flatnonzero(sig):
        if k <= busy or k + 1 >= n:
            continue
        e = o[k + 1]; tp, sl = TPK * w[k], SLK * w[k]
        end = min(n - 1, k + HOLD); r = None; j = end
        for j in range(k + 1, end + 1):
            if l[j] <= e * (1 - sl): r, why = -sl, "SL"; break
            if h[j] >= e * (1 + tp): r, why = tp, "TP"; break
        if r is None:
            if k + HOLD > n - 1:
                break  # still open at the end of data
            r, why, j = c[end] / e - 1, "TIME", end
        busy = j
        out.append(dict(mkt=mkt, sym=sym, entry_time=ot[k + 1], exit_time=ot[j] + 300000,
                        entry=e, w=w[k], tp_pct=tp, sl_pct=sl, exit=why, ret=r - FEE - slip))
    return out


# ---------------- portfolio ----------------
def portfolio(tr, capital, size, max_pos):
    tr = tr.sort_values(["entry_time", "sym"]).reset_index(drop=True)
    eq, open_pos, taken, curve = capital, [], [], [(tr.entry_time.min(), capital)]
    for _, t in tr.iterrows():
        for p in sorted([p for p in open_pos if p["exit_time"] <= t.entry_time], key=lambda p: p["exit_time"]):
            eq += p["pnl"]; open_pos.remove(p); curve.append((p["exit_time"], eq))
        if len(open_pos) >= max_pos:
            continue
        notional = size * eq
        pos = dict(t); pos.update(notional=notional, pnl=notional * t.ret)
        open_pos.append(pos); taken.append(pos)
    for p in sorted(open_pos, key=lambda p: p["exit_time"]):
        eq += p["pnl"]; curve.append((p["exit_time"], eq))
    curve = pd.DataFrame(curve, columns=["time", "equity"])
    return pd.DataFrame(taken), curve


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2026-03-01"); ap.add_argument("--end", default="2026-09-28")
    ap.add_argument("--capital", type=float, default=10000)
    ap.add_argument("--size", type=float, default=0.10, help="доля текущего депозита на сделку (0.10 = 10%%)")
    ap.add_argument("--max-pos", type=int, default=10, help="макс. одновременно открытых позиций")
    ap.add_argument("--slip", type=float, default=0.0, help="доп. издержки на сделку, напр. 0.001 = 0.1%%")
    ap.add_argument("--markets", default="spot,fut")
    ap.add_argument("--n-spot", type=int, default=90); ap.add_argument("--n-fut", type=int, default=70)
    ap.add_argument("--min-qv", type=float, default=2e6)
    ap.add_argument("--symbols", help="json-файл [[\"spot\",\"XXXUSDT\"],...] вместо автоподбора")
    ap.add_argument("--cache", default="vynos_data"); ap.add_argument("--out", default="vynos_result")
    a = ap.parse_args()

    start, end = pd.Timestamp(a.start), pd.Timestamp(a.end) + pd.Timedelta(days=1)
    os.makedirs(a.cache, exist_ok=True); os.makedirs(a.out, exist_ok=True)
    uni = json.load(open(a.symbols)) if a.symbols else pick_universe(a.n_spot, a.n_fut, a.min_qv)
    uni = [(m, s) for m, s in uni if m in a.markets.split(",")]
    print(f"universe: {len(uni)} pairs; downloading/caching data into {a.cache}/ ...", flush=True)
    s_ms, e_ms = start.tz_localize("UTC").value // 10**6, end.tz_localize("UTC").value // 10**6

    def job(ms):
        df = load_symbol(*ms, start, end, a.cache)
        if df is None:
            return []
        df = df[df.ot < e_ms].reset_index(drop=True)
        return [t for t in trades_for(df, *ms, a.slip) if t["entry_time"] >= s_ms]
    all_tr = []
    with cf.ThreadPoolExecutor(12) as ex:
        for i, r in enumerate(ex.map(job, uni), 1):
            all_tr += r
            if i % 20 == 0:
                print(f"  {i}/{len(uni)}", flush=True)
    tr = pd.DataFrame(all_tr)
    if tr.empty:
        sys.exit("no trades")
    taken, curve = portfolio(tr, a.capital, a.size, a.max_pos)
    for d in (tr, taken, curve):
        for col in ("entry_time", "exit_time", "time"):
            if col in d:
                d[col] = pd.to_datetime(d[col], unit="ms", utc=True)
    tr.to_csv(f"{a.out}/all_signals.csv", index=False)
    taken.to_csv(f"{a.out}/portfolio_trades.csv", index=False)
    curve.to_csv(f"{a.out}/equity.csv", index=False)

    eqv = curve.equity.values; dd = (eqv / np.maximum.accumulate(eqv) - 1).min()
    print(f"\nall signals: n={len(tr)} win={(tr.ret > 0).mean():.1%} avg={tr.ret.mean() * 100:+.3f}%/trade")
    print(f"portfolio  : taken {len(taken)} (skipped {len(tr) - len(taken)} — slots full), "
          f"size {a.size:.0%} of equity, max {a.max_pos} positions, slip {a.slip * 100:.2f}%")
    print(f"             win={(taken.ret > 0).mean():.1%}  start ${a.capital:,.0f} -> end ${eqv[-1]:,.0f}  "
          f"PnL ${eqv[-1] - a.capital:+,.0f} ({eqv[-1] / a.capital - 1:+.1%})  max drawdown {dd:.1%}")
    taken["month"] = taken.exit_time.dt.strftime("%Y-%m")
    m = taken.groupby("month").agg(n=("ret", "size"), win=("ret", lambda x: (x > 0).mean()), pnl=("pnl", "sum"))
    print("\nby month (exit):\n" + m.to_string(formatters={"win": "{:.1%}".format, "pnl": "${:+,.0f}".format}))
    k = taken.groupby("mkt").agg(n=("ret", "size"), win=("ret", lambda x: (x > 0).mean()), pnl=("pnl", "sum"))
    print("\nby market:\n" + k.to_string(formatters={"win": "{:.1%}".format, "pnl": "${:+,.0f}".format}))
    print(f"\nfiles: {a.out}/all_signals.csv, portfolio_trades.csv, equity.csv")


if __name__ == "__main__":
    main()
