"""Загрузка дневных данных (Yahoo, скорректированы на сплиты/дивиденды) с кэшем."""
import os
import pandas as pd
import yfinance as yf

CACHE = os.path.join(os.path.dirname(__file__), ".cache")

# Крупнейшие ликвидные акции NYSE/NASDAQ (≈S&P 100) + индексные ETF.
# ВНИМАНИЕ: список сегодняшний -> в бэктесте есть survivorship bias.
UNIVERSE = """AAPL MSFT NVDA AMZN GOOGL META BRK-B AVGO TSLA JPM LLY V UNH XOM MA JNJ PG HD COST
ABBV MRK CVX KO PEP ADBE WMT BAC CRM MCD CSCO ACN TMO ABT ORCL LIN DHR NKE TXN AMD WFC
DIS PM NEE VZ INTC CMCSA UPS RTX QCOM HON UNP IBM LOW AMGN SPGI CAT GS BA INTU ELV SBUX
PLD DE ISRG MDT GILD BLK AXP BKNG ADP MDLZ TJX SYK CVS MMC C REGN VRTX LMT MO SCHW CB CI
T SO DUK ZTS BMY MU AMAT LRCX ADI PANW KLAC SNPS CDNS MS USB PNC COP EOG SLB GE""".split()
ETFS = ["SPY", "QQQ", "IWM", "DIA"]


def load(tickers, start="2003-01-01"):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"ohlc_{len(tickers)}_{start}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    raw = yf.download(tickers, start=start, auto_adjust=True, progress=False,
                      group_by="column", threads=True)
    data = {f: raw[f] for f in ["Open", "High", "Low", "Close", "Volume"]}
    pd.to_pickle(data, path)
    return data
