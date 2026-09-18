"""Accumulation reference cases (KSMUSDT, GUSDT) + a broad episode study.

Accumulation is defined the way the live detector defines it:
  over a rolling window the pair is range-bound relative to its own recent
  volatility AND taker-buy flow is persistently tilted to the buy side AND the
  net buy flow is material in size.
"""
import io, json, sys, zipfile, datetime as dt
import numpy as np
import features as F
from binance_data import fetch_zip

WIN = 30          # minutes of accumulation window


def daily_1m(sym, day, market="spot"):
    key = (f"data/spot/daily/klines/{sym}/1m/{sym}-1m-{day}.zip" if market == "spot"
           else f"data/futures/um/daily/klines/{sym}/1m/{sym}-1m-{day}.zip")
    blob = fetch_zip(key)
    if blob is None:
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(z.namelist()[0]).decode()
    out = []
    for r in raw.strip().split("\n"):
        if not r or r.startswith("open_time"):
            continue
        c = r.split(",")
        t = int(c[0])
        if t > 1e15:
            t //= 1000
        out.append((t, float(c[1]), float(c[2]), float(c[3]), float(c[4]),
                    float(c[7]), float(c[8]), float(c[10])))
    return out


def load(sym, days, market="spot"):
    rows = []
    for d in days:
        r = daily_1m(sym, d, market)
        if r:
            rows += r
    if not rows:
        return None
    rows.sort()
    a = np.array(rows, dtype=float)
    return dict(ts=a[:, 0].astype(np.int64), open=a[:, 1], high=a[:, 2], low=a[:, 3],
                close=a[:, 4], qv=a[:, 5], trades=a[:, 6], tbq=a[:, 7])


def accum_state(d, f, win=WIN, taker_min=0.58, frac_min=0.65, flow_min=1.0, rng_max=0.9):
    tb = F._roll_sum(d["tbq"], win)
    qs = F._roll_sum(d["qv"], win)
    with np.errstate(invalid="ignore", divide="ignore"):
        taker_w = np.where(qs > 0, tb / qs, np.nan)
        flow = np.where(f["avg_min"] > 0, (2 * tb - qs) / (f["avg_min"] * win), np.nan)
        tk = np.where(d["qv"] > 0, d["tbq"] / d["qv"], np.nan)
    frac = F._roll_mean((np.nan_to_num(tk, nan=0.0) > 0.55).astype(float), win)
    rel = f["range15_rel"]
    st = ((np.nan_to_num(taker_w, nan=0) >= taker_min)
          & (np.nan_to_num(frac, nan=0) >= frac_min)
          & (np.nan_to_num(flow, nan=-9) >= flow_min)
          & (np.nan_to_num(rel, nan=9) <= rng_max))
    return dict(state=st, taker_w=taker_w, frac=frac, flow=flow, rel=rel)


def describe(sym, days, market="spot"):
    d = load(sym, days, market)
    if d is None:
        print(f"{sym} ({market}): NOT IN ARCHIVE for {days[0]}..{days[-1]}")
        return
    f = F.build(d)
    a = accum_state(d, f)
    st = a["state"]
    print(f"\n### {sym} ({market}) — {len(d['ts'])} bars, "
          f"{dt.datetime.utcfromtimestamp(d['ts'][0]/1000)} .. "
          f"{dt.datetime.utcfromtimestamp(d['ts'][-1]/1000)} UTC")
    # episodes = runs of the state, merged if separated by < 15 min
    idx = np.flatnonzero(st)
    if not len(idx):
        print("  no accumulation episodes under the production thresholds")
        top = np.nanargmax(np.nan_to_num(a["taker_w"], nan=0))
        print(f"  best taker_w{WIN}m of the window: {a['taker_w'][top]:.3f} at "
              f"{dt.datetime.utcfromtimestamp(d['ts'][top]/1000)} UTC "
              f"(flow ×{a['flow'][top]:.2f}, range15_rel {a['rel'][top]:.2f})")
        return
    eps, cur = [], [idx[0]]
    for i in idx[1:]:
        if i - cur[-1] <= 15:
            cur.append(i)
        else:
            eps.append(cur); cur = [i]
    eps.append(cur)
    print(f"  {len(eps)} episode(s)")
    for e in eps:
        i0, i1 = e[0], e[-1]
        seg = slice(max(i0 - WIN, 0), i1 + 1)
        fwd = slice(i1, min(i1 + 240, len(d["close"])))
        rng = (d["high"][seg].max() / d["low"][seg].min() - 1) * 100
        print(f"  {dt.datetime.utcfromtimestamp(d['ts'][i0]/1000):%m-%d %H:%M} .. "
              f"{dt.datetime.utcfromtimestamp(d['ts'][i1]/1000):%H:%M} UTC  "
              f"({i1-i0+1:>3} мин)  диапазон {rng:5.2f}%  "
              f"taker_w {np.nanmedian(a['taker_w'][e]):.3f}  "
              f"доля бычьих минут {np.nanmedian(a['frac'][e]):.2f}  "
              f"поток ×{np.nanmedian(a['flow'][e]):.2f}  "
              f"-> след. 4ч max {(d['high'][fwd].max()/d['close'][i1]-1)*100:+6.2f}%  "
              f"min {(d['low'][fwd].min()/d['close'][i1]-1)*100:+6.2f}%")


if __name__ == "__main__":
    days = [f"2026-09-{x:02d}" for x in range(15, 18)]
    for sym in ("KSMUSDT", "GUSDT"):
        for mkt in ("spot", "futures"):
            describe(sym, days, mkt)
