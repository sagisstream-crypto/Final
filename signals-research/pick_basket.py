"""Stratified basket of Binance spot USDT alts for the event study.

The scanner's `maxInitialVolume` filter is a 24h *quote* volume ceiling, so the
basket is chosen on the same axis: median daily quote volume per symbol.
"""
import json, statistics, random

MIN_MED = 300_000          # below this a 1m bar is mostly empty -> no usable signal
MAX_MED = 500_000_000      # the scanner's new ceiling
random.seed(7)

def main():
    v = json.load(open("cache/universe_volumes.json"))
    rows = sorted(((statistics.median(m.values()), s) for s, m in v.items()), reverse=True)
    rows = [r for r in rows if MIN_MED <= r[0] <= MAX_MED]
    bands = [(5e6, 1e18, 999), (1e6, 5e6, 26), (3e5, 1e6, 16)]
    picked = []
    for lo, hi, n in bands:
        pool = [r for r in rows if lo <= r[0] < hi]
        picked += pool if len(pool) <= n else random.sample(pool, n)
    picked.sort(reverse=True)
    json.dump([{"symbol": s, "med_daily_quote_vol": round(mv)} for mv, s in picked],
              open("cache/basket.json", "w"), indent=1)
    print(len(picked), "symbols")
    for mv, s in picked:
        print(f"  {s:<14} ${mv/1e6:9.2f}M/day")

if __name__ == "__main__":
    main()
