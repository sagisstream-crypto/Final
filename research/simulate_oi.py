"""Итоговая проверка правил с открытым интересом.
A: вход на закрытии свечи-триггера при фильтре (OI↑ на свече, толпа сокращает лонги, монета не перегрета);
   стоп под свечу-триггер, тейк 3R, через 30 мин выход, если цена ниже входа ИЛИ OI за 30 мин упал.
B: отложенный вход через 30 мин: стоп не выбит, цена выше входа, OI вырос ≥1% за 30 мин."""
import numpy as np, pandas as pd
import build_events as b
from simulate import FEE, SPLIT

def trade(h, l, c, i, e, stop, tp_r=3.0, exit_at=None, exit_if=None, hor=288):
    R = e - stop; tp = e + tp_r * R; end = min(i + hor, len(c) - 1); lo = 0.0
    for k in range(i + 1, end + 1):
        lo = min(lo, l[k] / e - 1)
        if l[k] <= stop: return stop / e - 1 - FEE, -1 - FEE * e / R, lo, (k - i) / 12
        if h[k] >= tp: return tp / e - 1 - FEE, tp_r - FEE * e / R, lo, (k - i) / 12
        if exit_at and k - i == exit_at and exit_if(c[k]):
            return c[k] / e - 1 - FEE, (c[k] - e) / R - FEE * e / R, lo, (k - i) / 12
    return c[end] / e - 1 - FEE, (c[end] - e) / R - FEE * e / R, lo, (end - i) / 12

E = pd.read_parquet("data/events_oi.parquet")
E = E[E.i < b.N - 300]
E["filt"] = (E.oi_bar >= 0.006) & (E.acc_ls_ch1h <= -0.026) & (E.r24h_pre <= 0.08)
out = []
for sym, g in E.groupby("sym"):
    a = b.load(sym); c, h, l = a["c"], a["h"], a["l"]
    for ev in g.itertuples():
        i, e = int(ev.i), ev.price; stop = l[i] * 0.998
        r = dict(sym=sym, t=ev.t, filt=ev.filt, notlate=ev.r24h_pre <= 0.08, risk=ev.a_risk, v24=ev.v24)
        oi_drop = (ev.oi_next30 < 0) if not np.isnan(ev.oi_next30) else False
        r["A_pct"], r["A_R"], r["A_lo"], r["A_h"] = trade(h, l, c, i, e, stop, exit_at=6,
                                                          exit_if=lambda px: px < e or oi_drop)
        j = i + 6
        okB = (l[i + 1:j + 1].min() > stop) and c[j] > e and ev.oi_next30 >= 0.01
        r["B_ok"] = okB
        if okB:
            r["B_pct"], r["B_R"], r["B_lo"], r["B_h"] = trade(h, l, c, j, c[j], stop, tp_r=2.0)
        out.append(r)
T = pd.DataFrame(out); T.to_parquet("data/sim_oi.parquet")

def line(d, col):
    d = d[d[f"{col}_pct"].notna()]
    days = d.t.dt.normalize(); daily = d.groupby(days)[f"{col}_pct"].sum().sort_values()
    wo = d[~days.isin(daily.tail(10).index)]
    return (f"n={len(d):5d} ({len(d)/max(days.nunique(),1):4.1f}/день) win={(d[f'{col}_pct']>0).mean()*100:4.1f}% "
            f"сред={d[f'{col}_pct'].mean()*100:+.3f}% медиана={d[f'{col}_pct'].median()*100:+.2f}% R={d[f'{col}_R'].mean():+.3f} "
            f"просадка мед={d[f'{col}_lo'].median()*100:5.2f}% хуже-5%={(d[f'{col}_lo']<=-0.05).mean()*100:4.1f}% "
            f"время мед={d[f'{col}_h'].median():.1f}ч | без топ10 дней {wo[f'{col}_pct'].mean()*100:+.3f}% +дни={(daily>0).mean()*100:.0f}%")
for name, m, col in [("A все триггеры", T.filt | ~T.filt, "A"), ("A фильтр OI+толпа+не поздно", T.filt, "A"),
                     ("B отложенный, не поздно", T.notlate & T.B_ok, "B"), ("B отложенный + фильтр", T.filt & T.B_ok, "B")]:
    print(f"{name:30s} TR", line(T[m & (T.t < SPLIT)], col)); print(f"{'':30s} TE", line(T[m & (T.t >= SPLIT)], col))
for lo, hi in [(1e6, 5e6), (5e6, 20e6), (20e6, 1e8)]:
    d = T[T.filt & T.v24.between(lo, hi)]
    print(f"объём {lo/1e6:.0f}–{hi/1e6:.0f}М", line(d, "A"))
