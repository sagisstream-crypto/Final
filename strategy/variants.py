import pandas as pd
from data import load, UNIVERSE, ETFS
from backtest import run, stats
pd.set_option("display.width", 250, "display.max_columns", 30, "display.float_format", "{:.3f}".format)
d = load(UNIVERSE + ETFS)
stocks = [t for t in UNIVERSE if d["Close"][t].notna().any()]
spy = d["Close"]["SPY"]
V = {
 "E ETF close,sma":   dict(tk=ETFS, max_pos=4, rsi_max=10, entry_close=True),
 "F ETF close,up":    dict(tk=ETFS, max_pos=4, rsi_max=10, entry_close=True, exit_mode="up"),
 "G stk close,sma":   dict(tk=stocks, rsi_max=5, entry_close=True),
 "H stk close,sma,rg":dict(tk=stocks, rsi_max=5, entry_close=True, regime=True),
 "I stk close,up,rg": dict(tk=stocks, rsi_max=5, entry_close=True, regime=True, exit_mode="up"),
 "J stk lim.5,sma,rg":dict(tk=stocks, rsi_max=5, limit_atr=0.5, regime=True),
 "K stk lim.5,up,rg": dict(tk=stocks, rsi_max=5, limit_atr=0.5, regime=True, exit_mode="up"),
 "L stk lim1,up,rg":  dict(tk=stocks, rsi_max=10, limit_atr=1.0, regime=True, exit_mode="up"),
}
rows=[]
for n,c in V.items():
    tk=c.pop("tk")
    for per,(s,e) in (("IS",("2005-01-01","2016-12-31")),("OOS",("2017-01-01",None))):
        eq,tr=run(d,tk,start=s,end=e,**c); rows.append(dict(s=n,p=per,**stats(eq,tr,spy)))
print(pd.DataFrame(rows).set_index(["s","p"])[["trades","winrate","avg_win","avg_loss","worst","profit_factor","CAGR","maxDD","sharpe","bench_CAGR"]].to_string())
