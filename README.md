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

## futures/ — derivatives data for USDT-M alt perps (same period)
- `funding/fund_{SYM}.parquet` — funding rate history (monthly archive, Oct 2025 – Aug 2026): `t` ms, `ih` funding interval hours, `fr` rate.
- `funding_sep/fundsep_{SYM}.parquet` — September 2026 funding (`t`, `fr`), not yet in the monthly archive at download time.
- `premium_1h/prem_{SYM}.parquet` — premium index 1h klines: `ot`, `po ph pl pc`.
- `metrics_full/met_{SYM}.parquet` — 5m open interest & ratios for ~100 most liquid perps, full year:
  `oi` (contracts), `oiv` (USDT value), `tt_acc` / `tt_pos` (top-trader long/short ratio by accounts / positions),
  `gl_acc` (global long/short account ratio), `taker` (taker buy/sell volume ratio).
- `metrics_partial/metd_{SYM}.parquet` — same columns, only a few days around candidate trades (322 symbols).
- `syms_by_qv.csv` — perps ranked by quote volume.

## btc/ — BTCUSDT spot 5m (Mar–Aug 2026) and 1h zips (Aug 2025 – Sep 2026) for regime filters.
