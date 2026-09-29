"""Verify l_best.signal reproduces the panel-based trades."""
import numpy as np, pandas as pd, lab10, l_best
T0 = pd.read_parquet("l_trades_stable.parquet")
rows = []
for m, s in lab10.all_syms():
    if m != "fut": continue
    H = lab10.load(m, s); mask, side = l_best.signal(H, m, s)
    if not mask.any(): continue
    res = np.fmin(H.up_t.values, H.dn_t.values); nxt = -1
    for i in np.nonzero(mask)[0]:
        if H.ot.values[i] < nxt or np.isnan(H.lab_short.values[i]): continue
        r = res[i]; nxt = H.ot.values[i] + 3600000 * (1 + (int(np.ceil(r / 60)) if np.isfinite(r) else 336))
        rows.append((s, H.ot.values[i], H.lab_short.values[i]))
d = pd.DataFrame(rows, columns=["sym", "ot", "lab"]); d["tr"] = d.ot < lab10.SPLIT
for k, x in d.groupby("tr"):
    w, l = (x.lab == 1).sum(), (x.lab == -1).sum(); print("TRAIN" if k else "TEST", len(x), round(w / (w + l), 3))
print("panel:", T0.groupby("tr").size().to_dict())
