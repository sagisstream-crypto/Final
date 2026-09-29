import numpy as np, pandas as pd
P = pd.read_parquet("sq_panel.parquet")
P = P[(P.qv24 >= 1e6) & P.f24.notna()]
SPLIT = pd.Timestamp("2026-05-01", tz="UTC").value // 10**6
HR = 3600000
def cooldown(d, hrs=24):
    d = d.sort_values(["sym", "ot"]); keep = np.zeros(len(d), bool); last = {}
    for i, (s, t) in enumerate(zip(d.sym.values, d.ot.values)):
        if t - last.get(s, -1e18) >= hrs * HR: keep[i] = True; last[s] = t
    return d[keep]
def stats(d):
    r = d.lab_long; res = r.isin([1, -1]); w = (r == 1).sum(); l = (r == -1).sum()
    fund24 = -np.nan_to_num(d.fr) * 3        # long receives -fr each 8h
    return dict(n=len(d), syms=d.sym.nunique(), f24=d.f24.mean() * 100, f24_med=d.f24.median() * 100,
                f24_fund=(d.f24 + fund24).mean() * 100, up10_24h=(d.mfe24 >= 0.10).mean() * 100,
                up20_72h=(d.mfe72 >= 0.20).mean() * 100, dn10_24h=(d.mae24 <= -0.10).mean() * 100,
                win10=w / max(res.sum(), 1) * 100)
C = {
 "ВСЕ часы (база)": None,
 "фандинг < −0.03%": P.fr < -0.0003,
 "фандинг < −0.1%": P.fr < -0.001,
 "фандинг < −0.3%": P.fr < -0.003,
 "фандинг < −1%": P.fr < -0.01,
 "шорт-аккаунтов больше (L/S < 1)": P.gl_acc < 0,
 "L/S аккаунтов < 0.8": P.gl_acc < np.log(0.8),
 "топ-трейдеры в шорте (поз. L/S < 1)": P.tt_pos < 0,
 "L/S резко упал (z < −2)": P.gl_acc_z < -2,
 "OI +15% за 24ч при падении цены >5%": (P.oi24 > np.log(1.15)) & (P.r24 < -0.05),
 "фандинг < −0.1% и OI +10% за 24ч": (P.fr < -0.001) & (P.oi24 > np.log(1.10)),
 "фандинг < −0.1% и цена держится (r24>0)": (P.fr < -0.001) & (P.r24 > 0),
 "фандинг < −0.1% и тейкеры покупают (>52%)": (P.fr < -0.001) & (P.tbr24 > 0.52),
 "фандинг < −0.1% и цена пробила 24ч-хай вверх": (P.fr < -0.001) & (P.r4 > 0.03),
 "фандинг < −0.3% и OI падает (шорты закрывают)": (P.fr < -0.003) & (P.oi4 < -0.03),
}
def main():
    rows = []
    for name, m in C.items():
        d = P if m is None else cooldown(P[m])
        for part, mm in (("подбор окт–апр", d.ot < SPLIT), ("проверка май–сен", d.ot >= SPLIT)):
            s = stats(d[mm]); s.update(условие=name, период=part); rows.append(s)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
    print(pd.DataFrame(rows).set_index(["условие", "период"]).round(2).to_string())
if __name__ == "__main__":
    main()
