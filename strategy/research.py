"""Исследование: in-sample 2005-2016, out-of-sample 2017-сегодня, сетка устойчивости."""
import itertools
import pandas as pd
from data import load, UNIVERSE, ETFS
from backtest import run, stats

pd.set_option("display.width", 200, "display.max_columns", 30,
              "display.float_format", "{:.3f}".format)
d = load(UNIVERSE + ETFS)
stocks = [t for t in UNIVERSE if t in d["Close"] and d["Close"][t].notna().any()]
spy = d["Close"]["SPY"]
IS, OOS = ("2005-01-01", "2016-12-31"), ("2017-01-01", None)

configs = {
    "A. ETF RSI2<10, вход open": dict(tk=["SPY", "QQQ", "IWM", "DIA"], rsi_max=10, max_pos=4),
    "B. Акции RSI2<5, вход open": dict(tk=stocks, rsi_max=5),
    "C. Акции RSI2<5, лимит -0.5ATR": dict(tk=stocks, rsi_max=5, limit_atr=0.5),
    "D. C + стоп 3ATR": dict(tk=stocks, rsi_max=5, limit_atr=0.5, stop_atr=3),
}
rows = []
for name, cfg in configs.items():
    tk = cfg.pop("tk")
    for per, (s, e) in (("IS", IS), ("OOS", OOS)):
        eq, tr = run(d, tk, start=s, end=e, **cfg)
        rows.append(dict(strategy=name, period=per, **stats(eq, tr, spy)))
    cfg["tk"] = tk
print(pd.DataFrame(rows).set_index(["strategy", "period"]).T)

print("\nУстойчивость (стратегия C, OOS): rsi_max x limit_atr -> winrate / PF / CAGR / maxDD")
grid = []
for rm, la in itertools.product([3, 5, 10], [0.0, 0.25, 0.5, 1.0]):
    eq, tr = run(d, stocks, rsi_max=rm, limit_atr=la, start=OOS[0])
    st = stats(eq, tr)
    grid.append(dict(rsi_max=rm, limit_atr=la, winrate=st["winrate"],
                     PF=st["profit_factor"], CAGR=st["CAGR"], maxDD=st["maxDD"],
                     trades=st["trades"]))
print(pd.DataFrame(grid).to_string(index=False))
