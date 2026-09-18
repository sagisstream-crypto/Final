#!/usr/bin/env python3
"""Extend event set to 12 months (2025-09-18 .. 2026-08-31) using existing cache.

Reference copy: kept as-is from the run that produced results_12m/. It hardcodes
/home/workdir/artifacts paths and monkey-patches binance_6m_runup_research's
globals (CACHE_DIR/START/END/periods_for) rather than using that script's own
--start/--end/--months flags -- adjust the paths/dates for your own machine
before reusing it. Note it also restricts candidates() to symbols that were
already eligible in the original 6-month screen, so a symbol that only became
thin (<=$2M 24h volume) in the extra Sep 2025-Feb 2026 window and never again
afterwards would be missed -- a full 1h re-screen over the whole 12 months
would be more correct if reusing this for a future extension.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, "/home/workdir/artifacts")
import binance_6m_runup_research as m

OUT = Path("/home/workdir/artifacts/binance_6m_results")
CACHE = Path("/home/workdir/artifacts/binance_6m_cache")
m.CACHE_DIR = CACHE
m.START = pd.Timestamp("2025-09-18 00:00:00", tz="UTC")
m.END = pd.Timestamp("2026-09-01 00:00:00", tz="UTC")

# Fix periods: monthly Sep 2025 through Aug 2026
def periods_monthly(_interval, monthly_only=True):
    return [x for x in m.months_between(m.START, pd.Timestamp("2026-09-01", tz="UTC")) if x != "2026-09"]

m.periods_for = periods_monthly


def candidates():
    pf = OUT / "processed_5m.txt"
    names = set()
    if pf.exists():
        names |= {x.strip() for x in pf.read_text().split() if x.strip()}
    ev = OUT / "runup_events.csv"
    if ev.exists():
        names |= set(pd.read_csv(ev)["symbol"].astype(str))
    sc = OUT / "symbol_screen.csv"
    if sc.exists():
        s = pd.read_csv(sc)
        s["eligible"] = s["eligible"].astype(str).str.lower().eq("true")
        s["min_24h_quote"] = pd.to_numeric(s["min_24h_quote"], errors="coerce")
        names |= set(s.loc[s.eligible & (s.min_24h_quote > 0) & s.symbol.str.endswith("USDT"), "symbol"].astype(str))
    return sorted(n for n in names if n.endswith("USDT"))


def main():
    import argparse, concurrent.futures as cf
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    cands = candidates()
    done_f = OUT / "processed_12m.txt"
    done = set(done_f.read_text().split()) if done_f.exists() else set()
    ev_path = OUT / "runup_events_12m.partial.csv"
    events = pd.read_csv(ev_path).to_dict("records") if ev_path.exists() and ev_path.stat().st_size else []
    if events:
        done |= {str(x.get("symbol")) for x in events}
    todo = [s for s in cands if s not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"12m candidates={len(cands)} done={len(done)} todo={len(todo)} months={periods_monthly('5m')}", flush=True)
    chunk = 12
    for i in range(0, len(todo), chunk):
        batch = todo[i : i + chunk]
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(m.load_symbol, s, "5m", True): s for s in batch}
            for fut in cf.as_completed(futs):
                s = futs[fut]
                try:
                    df = fut.result()
                    events.extend(m.extract_events(s, df))
                except Exception as e:
                    print("err", s, repr(e), flush=True)
        done |= set(batch)
        pd.DataFrame(events).to_csv(ev_path, index=False)
        done_f.write_text("\n".join(sorted(done)))
        print(f"12m processed={len(done)} events={len(events)} last={batch[0]}", flush=True)
    print("chunk_done", len(events), flush=True)


if __name__ == "__main__":
    main()
