"""Симуляция лонг-стратегий на триггерах «самое начало ралли».
Стоп — под минимум свечи-триггера; тейк — 3R; выход по времени — 24ч. Комиссия+проскальзывание 0.12% на круг.
Варианты: базовый, + «тайм-стоп» (если через N минут цена ниже входа — выходим), + фильтры контекста, отложенный вход."""
import numpy as np, pandas as pd
import build_events as b

FEE = 0.0012
SPLIT = pd.Timestamp("2026-06-01")


def trade(h, l, c, i, e, stop, tp_r=3.0, tstop=None, hor=288):
    """Возвращает (доход %, R-кратность, минимальная цена относительно входа за время сделки)."""
    R = e - stop
    tp = e + tp_r * R
    end = min(i + hor, len(c) - 1)
    lo = 0.0
    for k in range(i + 1, end + 1):
        lo = min(lo, l[k] / e - 1)
        if l[k] <= stop: return stop / e - 1 - FEE, -1 - FEE * e / R, lo
        if h[k] >= tp: return tp / e - 1 - FEE, tp_r - FEE * e / R, lo
        if tstop and k - i == tstop and c[k] < e:
            return c[k] / e - 1 - FEE, (c[k] - e) / R - FEE * e / R, lo
    return c[end] / e - 1 - FEE, (c[end] - e) / R - FEE * e / R, lo


def run():
    E = pd.read_parquet("data/events_r.parquet")
    E = E[E.i < b.N - 300]
    out = []
    for sym, g in E.groupby("sym"):
        a = b.load(sym); c, h, l, qv = a["c"], a["h"], a["l"], a["qv"]
        for ev in g.itertuples():
            i, e = int(ev.i), ev.price
            stop = l[i] * 0.998
            r = dict(sym=sym, t=ev.t, i=i, breadth=ev.breadth, btc_r1h=ev.btc_r1h, r24h_pre=ev.r24h_pre,
                     dist_hi7d=ev.dist_hi7d, hour=ev.hour, prev_trig_7d=ev.prev_trig_7d, risk=ev.a_risk)
            r["base_pct"], r["base_R"], r["base_lo"] = trade(h, l, c, i, e, stop)
            r["ts15_pct"], r["ts15_R"], r["ts15_lo"] = trade(h, l, c, i, e, stop, tstop=3)
            r["ts30_pct"], r["ts30_R"], r["ts30_lo"] = trade(h, l, c, i, e, stop, tstop=6)
            r["ts60_pct"], r["ts60_R"], r["ts60_lo"] = trade(h, l, c, i, e, stop, tstop=12)
            # отложенный вход: через 30 мин, если стоп не выбит, цена ≥ вход + 0.5R и объём держится ≥ 3× нормы
            j = i + 6
            base5 = qv[i] / ev.rvol5
            R0 = e - stop
            ok = l[i + 1:j + 1].min() > stop and c[j] >= e + 0.5 * R0 and qv[i + 1:j + 1].mean() >= 3 * base5
            r["delay_ok"] = ok
            if ok:
                r["delay_pct"], r["delay_R"], r["delay_lo"] = trade(h, l, c, j, c[j], stop, tp_r=2.0)
            out.append(r)
    T = pd.DataFrame(out)
    T.to_parquet("data/sim.parquet")
    return T


def summary(T, col, mask=None, label=""):
    d = T if mask is None else T[mask]
    d = d[d[f"{col}_pct"].notna()]
    def one(x):
        if len(x) == 0: return "n=0"
        days = max(x.t.dt.normalize().nunique(), 1)
        return (f"n={len(x):6d} ({len(x)/days:4.1f}/день) win={(x[f'{col}_pct'] > 0).mean()*100:5.1f}% "
                f"сред={x[f'{col}_pct'].mean()*100:+.3f}% R={x[f'{col}_R'].mean():+.3f} "
                f"просадка медиана={x[f'{col}_lo'].median()*100:5.2f}% хуже-5%={(x[f'{col}_lo'] <= -0.05).mean()*100:4.1f}%")
    return (f"{label:34s} train {one(d[d.t < SPLIT])}\n{'':34s} test  {one(d[d.t >= SPLIT])}")


if __name__ == "__main__":
    import sys
    T = run() if "--rerun" in sys.argv or True else pd.read_parquet("data/sim.parquet")
    notlate = T.r24h_pre <= 0.08
    mkt = T.breadth >= 0.027
    for col in ["base", "ts15", "ts30", "ts60"]:
        print(summary(T, col, None, f"{col} все"))
        print(summary(T, col, notlate, f"{col} не поздно (r24h≤8%)"))
        print(summary(T, col, notlate & mkt, f"{col} не поздно + рынок"))
    print(summary(T, "delay", None, "отложенный вход 30м"))
    print(summary(T, "delay", notlate, "отложенный + не поздно"))
    print(summary(T, "delay", notlate & mkt, "отложенный + не поздно + рынок"))
