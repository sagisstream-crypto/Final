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

## score_v1: a candidate scoring rubric, tested against a real run

`score_v1.py` takes `runup_events.csv` from an actual run of the script above
and scores every event with a hand-built rubric (impulse size, candle shape,
RVOL, 5m liquidity, a combo bonus, and penalties — see the file for the exact
bands), then checks whether higher SCORE actually means a higher share of
`REAL_CONTINUATION` events.

```bash
python score_v1.py --in binance_results/runup_events.csv --out score_v1_results
```

`score_v1_results/` holds a real run: **193 events across 85 low-liquidity
USD-M symbols, March–August 2026.** Its own report already flags the
headline numbers as in-sample (the rubric was written looking at this same
data), so `score_v1_time_split_check.py` re-checks the same events split by
time — March–June (n=122, the period the rubric was effectively eyeballed
against) vs held-out July–August (n=71):

```bash
python score_v1_time_split_check.py --in score_v1_results/score_v1_events.csv --split 2026-07-01
```

**What held up out-of-time (July–Aug repeats the March–June pattern):**
- SCORE ≥60 gives ~2x lift over baseline in both periods; ≥70/≥80 hold or
  improve in the held-out period.
- Closing near the candle high with a wide body (`close_pos`, `body_pct`) is
  consistently higher for `REAL_CONTINUATION` in both periods — this matches
  the MYX/EVAA candle-shape logic already added to `index.html`'s SCORE.
- **Tight 12h compression before the impulse (`range12_pct` ≤ 4%) predicts a
  *lower* real-continuation rate, not higher, in both periods** (11%/7% vs
  21%/23% for a wider pre-range). This contradicts VolScan's current "тихая
  полка" bonus (+12 points in `computeScore()`), which rewards exactly the
  opposite.
- `taker_buy_share` medians are nearly identical between the two labels in
  both periods — no real discriminative power here, at least on this sample.
  `score_v1`'s rubric doesn't use it as a component at all.
- High `vol_accel_1h` without a confirming candle shape leans towards
  `FAILED_OR_PARTIAL`, not `REAL_CONTINUATION`, in both periods.

**What's still shaky:** RVOL's direction flips between the two periods
(clearly higher for REAL in March–June, roughly flat/reversed in July–Aug) —
don't lean on it hard. And 122/71 events is still a small, single time-split
sample, not a walk-forward validation.

**Status:** `index.html`'s live SCORE has *not* been changed based on this —
the decision (2026-09-18) was to wait for more months of data before
touching the live scanner, given the sample size above. When more data is
available, rerun `binance_runup_research.py` for a longer/fresher window,
then `score_v1.py` and `score_v1_time_split_check.py` again, before adjusting
`computeScore()` in `index.html` (candidates per the findings above: soften
or drop the "тихая полка" bonus, de-weight `taker`, keep/strengthen the
close_pos/body% component, keep capping the RVOL bonus at extreme values).
