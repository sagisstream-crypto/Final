import numpy as np, pandas as pd
E = pd.read_parquet("data/events.parquet")
FEE = 0.0012
def add(E, p):
    R = E[f"{p}risk"]
    E[f"{p}real"] = E[f"{p}rmax"] >= 3
    E[f"{p}noise"] = (E[f"{p}rmax"] < 1) & (E[f"{p}stopped"] == 1)
    # стратегия: TP 3R / SL -1R / выход через 24ч; в % от депо на сделку с риском R
    t = np.where(E[f"{p}rmax"] >= 3, 3, np.where(E[f"{p}stopped"] == 1, -1, (E[f"{p}ret24h"] / R).clip(-1, 3)))
    E[f"{p}evR"] = t - FEE / R          # комиссия в R
    E[f"{p}ev2R"] = np.where(E[f"{p}rmax"] >= 2, 2, np.where(E[f"{p}stopped"] == 1, -1, (E[f"{p}ret24h"] / R).clip(-1, 2))) - FEE / R
add(E, "a_"); add(E, "b_")
E = E[(E.a_risk >= 0.004) & (E.b_risk >= 0.004)]
E.to_parquet("data/events_r.parquet")
def st(d, p="a_"):
    return dict(n=len(d), real=round(d[f"{p}real"].mean()*100,1), noise=round(d[f"{p}noise"].mean()*100,1),
                stop=round(d[f"{p}stopped"].mean()*100,1), evR=round(d[f"{p}evR"].mean(),3), ev2R=round(d[f"{p}ev2R"].mean(),3),
                risk=round(d[f"{p}risk"].median()*100,2), deep10=round((d[f"{p}mae24h"]<=-0.10).mean()*100,1))
if __name__ == "__main__":
    print("A", st(E,"a_")); print("B", st(E,"b_"))
    feats = ["r5","r5_z","rvol5","rtrades5","ats_ratio","taker5","clv","upper_wick","brk24h","brk7d","r1h_pre","r4h_pre",
             "r24h_pre","r7d_pre","dist_hi30d","dist_hi7d","rng72_pre","sd5_pre","quiet6h","v24","v24_vs_7d","age_days",
             "prev_trig_7d","hour","btc_r1h","btc_r24h","breadth","a_risk"]
    for f in feats:
        x = E[f]
        if x.nunique() <= 3: g = x
        else: g = pd.qcut(x, 5, duplicates="drop")
        print(f"\n[{f}]")
        for k, d in E.groupby(g, observed=True): print("  %-24s"%str(k), st(d))
    print("\n=== confirmation (entry B)")
    for f in ["c_ret","c_pull","c_newhigh","c_vol","c_rvol","c_taker","c_green","b_risk"]:
        x = E[f]; g = x if x.nunique() <= 3 else pd.qcut(x, 5, duplicates="drop")
        print(f"\n[{f}]")
        for k, d in E.groupby(g, observed=True): print("  %-24s"%str(k), st(d,"b_"))
