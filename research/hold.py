import lib, pandas as pd, numpy as np
lib.D = lib.D.replace("/data", "/data_hold"); lib.SPLIT = pd.Timestamp("2026-09-01", tz="UTC").value // 10**6
import vynos_signal as a_best, rejected_market_flush as b_best
def detail(t, name):
    t = t[t.ot >= lib.SPLIT]
    for k, g in [("ALL", t)] + list(t.groupby("mkt")):
        print(f"  {name} SEP {k}: n={len(g)} win={(g.ret>0).mean():.1%} ev={g.ret.mean()*100:+.3f}% sum={g.ret.sum()*100:+.1f}%")
    if len(t):
        bys = t.groupby("sym").ret.sum().sort_values(ascending=False); days = pd.to_datetime(t.ot, unit="ms").dt.date
        print(f"  top5 sym share={bys.head(5).sum()/t.ret.sum():.0%}  days={days.nunique()}  sym>0={(t.groupby('sym').ret.sum()>0).mean():.0%}")
ta = lib.run_all(a_best.signal, None, None, a_best.HOLD, label="A sweep-reclaim  Aug||Sep"); detail(ta, "A")
tb = lib.run_all(b_best.signal, None, None, b_best.HOLD, label="B market-flush   Aug||Sep"); detail(tb, "B")
ta.to_pickle("hold_a.pkl"); tb.to_pickle("hold_b.pkl")
