"""Что происходит через 15/30/60 мин после триггера и как это меняет шансы.
Для каждого события: выжила ли сделка (стоп под свечой-триггером не выбит), где цена в R, держится ли объём.
Дальше: P(дойти до 3R | состояние в момент T) — исход считается от входа на свече-триггере."""
import numpy as np, pandas as pd
import build_events as b
E = pd.read_parquet("data/events_r.parquet")
rows = []
for sym, g in E.groupby("sym"):
    a = b.load(sym)
    c, h, l, qv = a["c"], a["h"], a["l"], a["qv"]
    for _, ev in g[g.i < b.N - 20].iterrows():
        i = int(ev.i); e = ev.price; stop = l[i] * 0.998; R = e - stop
        base = qv[i] / ev.rvol5 if ev.rvol5 > 0 else np.nan
        for m in (3, 6, 12):   # 15, 30, 60 мин
            seg_l = l[i + 1:i + 1 + m]; seg_h = h[i + 1:i + 1 + m]
            rows.append(dict(sym=sym, i=i, t=ev.t, T=m * 5, alive=float(seg_l.min() > stop),
                             posR=(c[i + m] - e) / R, maxR=(seg_h.max() - e) / R,
                             vol_hold=qv[i + 1:i + 1 + m].mean() / base if base > 0 else np.nan,
                             real=ev.a_real, rmax=ev.a_rmax, deep10=float(ev.a_mae24h <= -0.10),
                             breadth=ev.breadth, r24h_pre=ev.r24h_pre, risk=ev.a_risk, ret24h=ev.a_ret24h))
S = pd.DataFrame(rows)
S.to_parquet("data/survival.parquet")
