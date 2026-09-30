"""Сигналы на сегодня: лучшая по OOS версия с винрейтом ~80%.
Правила: цена > SMA200, SPY > SMA200, RSI(2) < 5, оборот > $20M/день.
Вход — MOC по закрытию, выход — первое закрытие с прибылью >0.3% или через 10 дней.
"""
import pandas as pd
import yfinance as yf
from data import UNIVERSE
from backtest import rsi

d = yf.download(UNIVERSE + ["SPY"], period="2y", auto_adjust=True, progress=False)
C, V = d["Close"], d["Volume"]
spy = C["SPY"]
regime = spy.iloc[-1] > spy.rolling(200).mean().iloc[-1]
r2 = rsi(C, 2).iloc[-1]
ok = (C.iloc[-1] > C.rolling(200).mean().iloc[-1]) & (r2 < 5) & \
     ((C * V).rolling(20).mean().iloc[-1] > 2e7)
print(f"Дата: {C.index[-1].date()}  SPY>SMA200: {regime}")
if not regime:
    print("Рынок ниже SMA200 — новых входов нет.")
print(r2[ok].drop("SPY", errors="ignore").sort_values().head(10).rename("RSI2").to_string())
