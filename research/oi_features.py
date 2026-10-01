"""Признаки позиционирования на момент закрытия свечи-триггера (без заглядывания вперёд)
и через 30 минут (для правила удержания)."""
import os
import numpy as np, pandas as pd
E = pd.read_parquet("data/events_r.parquet")
S = pd.read_parquet("data/sim.parquet")[["sym", "i", "base_pct", "base_R", "base_lo", "ts30_pct", "ts30_R", "ts30_lo"]]
E = E.merge(S, on=["sym", "i"], how="left")
rows = []
for sym, g in E.groupby("sym"):
    p = f"data/metrics/{sym}.parquet"
    if not os.path.exists(p): continue
    M = pd.read_parquet(p).set_index("t").sort_index()
    M = M[~M.index.duplicated()]
    def at(ts, col):
        k = M.index.searchsorted(ts, side="right") - 1
        if k < 0 or (ts - M.index[k]) > pd.Timedelta(minutes=15): return np.nan
        return M[col].iloc[k]
    for ev in g.itertuples():
        t = ev.t + pd.Timedelta(minutes=5)        # закрытие свечи-триггера
        oi = at(t, "sum_open_interest")
        f = lambda dt, col="sum_open_interest": at(t - dt, col)
        rows.append(dict(sym=sym, i=ev.i,
            oi_bar=oi / f(pd.Timedelta(minutes=5)) - 1,
            oi_1h=oi / f(pd.Timedelta(hours=1)) - 1,
            oi_4h=oi / f(pd.Timedelta(hours=4)) - 1,
            oi_24h=oi / f(pd.Timedelta(hours=24)) - 1,
            oi_to_vol=at(t, "sum_open_interest_value") / ev.v24,
            top_ls=at(t, "sum_toptrader_long_short_ratio"),
            top_ls_ch1h=at(t, "sum_toptrader_long_short_ratio") / f(pd.Timedelta(hours=1), "sum_toptrader_long_short_ratio") - 1,
            acc_ls=at(t, "count_long_short_ratio"),
            acc_ls_ch1h=at(t, "count_long_short_ratio") / f(pd.Timedelta(hours=1), "count_long_short_ratio") - 1,
            oi_next30=at(t + pd.Timedelta(minutes=30), "sum_open_interest") / oi - 1,
        ))
F = pd.DataFrame(rows)
E = E.merge(F, on=["sym", "i"], how="inner")
E.to_parquet("data/events_oi.parquet")
print(len(E), E[[c for c in F.columns if c not in ("sym", "i")]].describe().T.round(4).to_string())
