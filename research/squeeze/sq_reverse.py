import numpy as np, pandas as pd
P = pd.read_parquet("sq_panel.parquet"); P = P[(P.qv24 >= 1e6) & P.mfe24.notna()]
SPLIT = pd.Timestamp("2026-05-01", tz="UTC").value // 10**6
M = P[P.oi24.notna() & P.oi_f24.notna()].copy()
M["sq"] = (M.mfe24 >= 0.15) & (M.oi_f24 < np.log(0.9))       # real squeeze: +15% within 24h while OI drops >10%
M["pump"] = (M.mfe24 >= 0.15) & ~M.sq                          # +15% but OI not collapsing (new longs)
print(f"liquid fut hours with OI data: {len(M):,} on {M.sym.nunique()} perps; squeeze-start hours {M.sq.mean()*100:.2f}%, other +15% pumps {M.pump.mean()*100:.2f}%")
C = {"фандинг < −0.03%": M.fr < -0.0003, "фандинг < −0.1%": M.fr < -0.001, "фандинг < −0.3%": M.fr < -0.003,
     "L/S аккаунтов < 1": M.gl_acc < 0, "топ-трейдеры в шорте": M.tt_pos < 0, "L/S z < −2": M.gl_acc_z < -2,
     "OI +10% за 24ч": M.oi24 > np.log(1.1), "OI +10% и цена −5%": (M.oi24 > np.log(1.1)) & (M.r24 < -0.05),
     "фандинг<−0.1% и OI +10%": (M.fr < -0.001) & (M.oi24 > np.log(1.1)),
     "фандинг<−0.1% и L/S<1": (M.fr < -0.001) & (M.gl_acc < 0)}
rows = []
for name, m in C.items():
    for part, pm in (("окт–апр", M.ot < SPLIT), ("май–сен", M.ot >= SPLIT)):
        d = M[pm]; mm = m[pm]; base = d.sq.mean()
        p_sq = d.sq[mm].mean(); share = mm[d.sq].mean()
        dn = (d.mae24[mm] <= -0.15).mean()
        rows.append(dict(условие=name, период=part, часов=int(mm.sum()), **{"P(сквиз)%": p_sq * 100, "база%": base * 100,
                    "лифт×": p_sq / base, "доля сквизов с условием%": share * 100, "P(−15% за 24ч)%": dn * 100}))
pd.set_option("display.width", 250)
print(pd.DataFrame(rows).set_index(["условие", "период"]).round(2).to_string())
# what the average squeeze looked like beforehand
s = M[M.sq]; o = M[~M.sq]
cols = ["fr", "r24", "r72", "oi24", "oi72", "gl_acc", "tt_pos", "vol24", "tbr24"]
print("\nсредние ДО сквиза vs обычные часы:"); print(pd.DataFrame({"сквиз": s[cols].median(), "обычно": o[cols].median()}).round(4).to_string())
