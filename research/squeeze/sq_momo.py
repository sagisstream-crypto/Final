import numpy as np, pandas as pd
from sq_study import cooldown, stats, SPLIT
P = pd.read_parquet("sq_panel.parquet"); P = P[(P.qv24 >= 1e6) & P.f24.notna()]
M = P[P.gl_acc.notna()]
C = {"база (перпы с данными L/S)": None,
     "рост 72ч > +10%": M.r72 > 0.10,
     "рост 72ч > +10% и L/S < 1": (M.r72 > 0.10) & (M.gl_acc < 0),
     "рост 72ч > +10%, L/S < 1, OI 72ч > +5%": (M.r72 > 0.10) & (M.gl_acc < 0) & (M.oi72 > np.log(1.05)),
     "рост 72ч > +10%, L/S < 1, фандинг < 0": (M.r72 > 0.10) & (M.gl_acc < 0) & (M.fr < 0),
     "рост 72ч > +10% и L/S вырос (≥1)": (M.r72 > 0.10) & (M.gl_acc >= 0)}
rows = []
for name, m in C.items():
    d = M if m is None else cooldown(M[m])
    for part, mm in (("окт–апр", d.ot < SPLIT), ("май–сен", d.ot >= SPLIT)):
        s = stats(d[mm]); s.update(условие=name, период=part); rows.append(s)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
print(pd.DataFrame(rows).set_index(["условие", "период"]).round(2).to_string())
