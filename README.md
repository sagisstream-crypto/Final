# market-data — Binance alt klines (5m), Oct 2025 – Sep 28 2026

Data branch for the research in `research/` on the main branch. Source: public archive https://data.binance.vision.

- `klines_5m/{spot|fut}_{SYMBOL}.parquet` — 1205 USDT alt pairs (420 spot + 785 USDT-M perps), incl. pairs
  delisted during the period; BTC/ETH/BNB/SOL/XRP/DOGE/other majors and stablecoins excluded.
  Columns: `ot` (5m bar open time, ms UTC), `o h l c` (price), `qv` (quote volume, USDT), `n` (trades),
  `tbqv` (taker-buy quote volume). Covers 2025-10-01 … 2026-09-28 (Sept from daily files).
- `lab10.py` — builds hourly bars + "+10% before −10%" labels from these files (set SRC to `klines_5m`).

Use with the backtest (main branch): `python research/backtest_vynos.py --local klines_5m --symbols list.json`
or just `pandas.read_parquet(...)`.

Clone only this branch: `git clone --single-branch -b market-data https://github.com/sagisstream-crypto/Final`
