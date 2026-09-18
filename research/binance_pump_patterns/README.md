# Binance USD-M Futures — real run-up vs fake spike research

Research tool for the question behind VolScan's SCORE: on low-liquidity USD-M
futures (24h quote volume under a small ceiling), what actually separates a
pair that keeps running from one that spikes on volume and gives it all back?

This is **not** a scanner and it does not generate trading signals. It downloads
historical klines from the official [Binance public data archive](https://github.com/binance/binance-public-data)
and produces CSVs + an HTML report comparing pre-impulse conditions between two
research-labeled groups of events, `REAL_CONTINUATION` vs `FAILED_OR_PARTIAL`.

## Why this exists

Two screenshots (MYXUSDT, EVAAUSDT) triggered the original question: a bigger
5m volume spike (MYX, 2.91M) produced a smaller follow-through than a smaller
one (EVAA, 1.40M). Raw volume size is not the differentiator — this script
exists to find, from real archived data instead of two examples, which
pre-impulse and impulse-candle features (RVOL, volume acceleration, 12h
compression, candle body/wick shape, taker-buy share, trade-count
acceleration) actually correlate with continuation.

## Requirements

- Python 3.9+
- Real internet access to `data.binance.vision` (and its S3 listing endpoint).
  A 6-month window across the full USD-M archive downloads several GB — run it
  on a machine with a normal connection and some free disk, not inside a
  network-restricted sandbox/CI runner.

```bash
python -m pip install -r requirements.txt
```

## Running it

```bash
python binance_runup_research.py --workers 8
```

Defaults to a trailing 6-month window ending now (UTC) and a $2,000,000
rolling-24h-quote-volume ceiling — matching the original ask ("all futures
with 24h volume under $2M over the last 6 months"). Useful overrides:

```bash
# Fixed window instead of "6 months back from today"
python binance_runup_research.py --start 2026-03-18 --end 2026-09-19

# Different liquidity ceiling / impulse thresholds
python binance_runup_research.py --max-24h-usd 1000000 --min-rvol 4

# Quick smoke test on a handful of symbols before committing to a full run
python binance_runup_research.py --max-symbols 20
```

All thresholds (impulse size, RVOL floor, the `REAL_CONTINUATION` research
label's gain/adverse-move definition) are CLI flags — see `--help`. The
`REAL_CONTINUATION` label is a research convention (first reaches +X% within
N hours without a -Y% adverse move first), not an objective truth about the
market; change it and rerun to test other definitions.

## Method

1. **1h screening** across the whole historical USD-M symbol list to find
   which symbols could ever have had rolling 24h quote volume under the
   ceiling (a 24h rolling sum can only be low if every constituent 1h bar is).
2. **5m research** only on those candidates: detect impulses (≥5% candle body
   or ≥8% high excursion, with RVOL ≥ 3, while 24h quote volume stays under
   the ceiling), then measure pre-impulse compression, volume/trade
   acceleration, candle shape (body %, close position, wick), taker-buy share,
   and forward MFE/MAE at 30m/1h/2h/4h horizons.
3. Label each event `REAL_CONTINUATION` or `FAILED_OR_PARTIAL` and compare the
   two groups' medians/p75s in `runup_summary.csv`.

## Output

- `report.html` — comparison table + largest observed continuation events.
- `runup_events.csv` — every detected impulse with its full feature set.
- `runup_summary.csv` — `REAL_CONTINUATION` vs `FAILED_OR_PARTIAL` medians/p75s.
- `symbol_screen.csv` — which symbols passed the 24h-volume pre-filter.
- `errors.csv` — archive files that failed to download (Binance's archive can
  have gaps; these are recorded, not silently skipped).

## Feeding results back into the scanner

`../../index.html` (VolScan) already implements a first qualitative pass at
this, ahead of running the actual 6-month study:

- Its composite SCORE now reads USDT-M futures taker-buy share too (the hot
  `kline_1m` stream previously only ever opened against the spot host and
  filtered to `market === "SPOT"` rows, so every futures pair scored with
  `taker: null`).
- SCORE now includes a candle-shape component: closing near the 1m candle's
  high with a small upper wick adds points; closing in the lower part of the
  range with a long upper wick (the MYX/EVAA "spike then give it back" shape)
  subtracts points.

Once this script has been run for real, use `runup_summary.csv` to check
whether those thresholds (and the existing RVOL/taker/spread bands in
`computeScore()` in `index.html`) actually hold up, and adjust the weights —
they're deliberately kept in one function so they can be tuned from data
instead of by eye.
