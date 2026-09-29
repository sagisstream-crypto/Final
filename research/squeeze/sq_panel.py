"""Short-squeeze study panel: hourly fut features (data_extra_d/feat.parquet) + forward outcomes from h10 hourly bars."""
import numpy as np, pandas as pd, lab10
F = pd.read_parquet("data_extra_d/feat.parquet")
outs = []
for s, g in F.groupby("sym", sort=False):
    H = pd.read_parquet(f"{lab10.DST}/fut_{s}.parquet", columns=["ot", "h", "l", "c", "entry"])
    H = H.set_index("ot").reindex(g.ot.values)
    e = H.entry.values; c = H.c.values; h = H.h.values; l = H.l.values
    o = {"ot": g.ot.values, "sym": s}
    lc = np.log(c)
    for k in (4, 24, 72):
        o[f"f{k}"] = np.r_[lc[k:], np.full(k, np.nan)] - np.log(e)       # entry -> close k hours later
    for k in (24, 72):
        hh = pd.Series(h[::-1]).rolling(k, min_periods=k).max().values[::-1]  # max high of hours t..t+k-1
        ll = pd.Series(l[::-1]).rolling(k, min_periods=k).min().values[::-1]
        o[f"mfe{k}"] = np.r_[hh[1:], np.nan] / e - 1                       # hours t+1..t+k
        o[f"mae{k}"] = np.r_[ll[1:], np.nan] / e - 1
    outs.append(pd.DataFrame(o))
P = F.merge(pd.concat(outs, ignore_index=True), on=["ot", "sym"])
# future OI change (to identify actual squeezes = price up while OI down)
P = P.sort_values(["sym", "ot"])
P["oi_f24"] = P.groupby("sym").oi24.shift(-24)
for c in P.columns:
    if P[c].dtype == np.float64: P[c] = P[c].astype(np.float32)
P.to_parquet("sq_panel.parquet"); print(P.shape)
