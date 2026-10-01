"""Итоговое правило входа «ралли, а не шум» и его помесячный результат.
Фильтр на закрытии первой 5м свечи-выброса (+1.5% и объём ≥5× нормы, объём 24ч 1–100М):
  1) OI за эту свечу вырос ≥ +0.6%
  2) доля аккаунтов в лонге (global long/short account ratio) за час упала ≤ −2.6%
  3) монета за предыдущие сутки выросла не больше чем на 8%
  4) стоп под минимум свечи-триггера не дальше 4% от входа
Выход: стоп −1R; тейк +3R; через 30 мин — выход, если цена ниже входа или OI за 30 мин упал; иначе максимум 24ч."""
import numpy as np, pandas as pd
import build_events as b
from simulate import FEE, SPLIT

OI_BAR, ACC_1H, MAX_R24, MAX_RISK = 0.006, -0.026, 0.08, 0.04


def trade(o, h, l, c, i, e, stop, oi_drop, tp_r=3.0, hor=288, slip=0.0005):
    R = e - stop; tp = e + tp_r * R; lo = 0.0
    for k in range(i + 1, min(i + hor, len(c) - 1) + 1):
        lo = min(lo, l[k] / e - 1)
        if l[k] <= stop: return min(stop, o[k]) * (1 - slip) / e - 1 - FEE, lo, "стоп", (k - i) / 12
        if h[k] >= tp: return tp / e - 1 - FEE, lo, "тейк 3R", (k - i) / 12
        if k - i == 6 and (c[k] < e or oi_drop): return c[k] / e - 1 - FEE - slip, lo, "выход 30м", (k - i) / 12
    return c[k] / e - 1 - FEE, lo, "24ч", (k - i) / 12


if __name__ == "__main__":
    E = pd.read_parquet("data/events_oi.parquet"); E = E[E.i < b.N - 300]
    E = E[(E.oi_bar >= OI_BAR) & (E.acc_ls_ch1h <= ACC_1H) & (E.r24h_pre <= MAX_R24) & (E.a_risk <= MAX_RISK)]
    rows = []
    for sym, g in E.groupby("sym"):
        a = b.load(sym)
        for ev in g.itertuples():
            i = int(ev.i)
            p, lo, why, hrs = trade(a["o"], a["h"], a["l"], a["c"], i, ev.price, a["l"][i] * 0.998, ev.oi_next30 < 0)
            rows.append(dict(sym=sym, t=ev.t, pct=p, lo=lo, exit=why, hrs=hrs, v24=ev.v24))
    T = pd.DataFrame(rows).sort_values("t")
    T.to_csv("data/final_trades.csv", index=False)
    def s(d):
        w, l = d.pct[d.pct > 0].sum(), -d.pct[d.pct < 0].sum()
        return pd.Series(dict(сделок=len(d), в_день=round(len(d) / d.t.dt.normalize().nunique(), 1),
                              прибыльных=round((d.pct > 0).mean() * 100, 1), средний=round(d.pct.mean() * 100, 2),
                              PF=round(w / l, 2), худшая=round(d.pct.min() * 100, 1)))
    print(T.groupby(T.t.dt.to_period("M")).apply(s).to_string())
    print("\nобучение (окт–май):\n", s(T[T.t < SPLIT]).to_string())
    print("\nпроверка (июн–сен):\n", s(T[T.t >= SPLIT]).to_string())
    print("\nвесь год:\n", s(T).to_string())
    print("\nвыходы:\n", T.groupby("exit").pct.agg(["size", "mean"]).assign(mean=lambda x: (x["mean"] * 100).round(2)).to_string())
    print("\nтоп-10 монет по числу сделок:", T.sym.value_counts().head(10).to_dict())
