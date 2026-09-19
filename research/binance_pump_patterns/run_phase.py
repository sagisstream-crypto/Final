#!/usr/bin/env python3
"""Resume helper: continue 1h screen in small batches OR run 5m events on known eligible USDT names.

Reference copy, kept as-is: hardcodes /home/workdir/artifacts paths and imports
binance_6m_runup_research as a module. Useful as a resumable-batch pattern for a
slow multi-day download rather than a drop-in script -- adjust paths for your own
machine.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, "/home/workdir/artifacts")
import binance_6m_runup_research as m

OUT = Path("/home/workdir/artifacts/binance_6m_results")
CACHE = Path("/home/workdir/artifacts/binance_6m_cache")
m.CACHE_DIR = CACHE
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)


def load_screen():
    for src in (OUT / "symbol_screen.csv", OUT / "symbol_screen.partial.csv"):
        if src.exists() and src.stat().st_size:
            return pd.read_csv(src)
    return pd.DataFrame()


def eligible_usdt(sdf: pd.DataFrame):
    if sdf.empty:
        return []
    el = sdf.eligible.astype(str).str.lower().eq("true")
    q = pd.to_numeric(sdf.min_24h_quote, errors="coerce")
    names = sdf.loc[el & (q > 0) & sdf.symbol.astype(str).str.endswith("USDT"), "symbol"].astype(str)
    return sorted(set(names))


def phase_5m(limit=0, workers=10):
    import concurrent.futures as cf
    sdf = load_screen()
    cands = eligible_usdt(sdf)
    processed = set()
    pf = OUT / "processed_5m.txt"
    if pf.exists():
        processed |= set(pf.read_text().split())
    ev_path = OUT / "runup_events.partial.csv"
    events = []
    if ev_path.exists() and ev_path.stat().st_size:
        events = pd.read_csv(ev_path).to_dict("records")
        processed |= {str(x.get("symbol")) for x in events}
    todo = [s for s in cands if s not in processed]
    if limit:
        todo = todo[:limit]
    print(f"5m candidates={len(cands)} processed={len(processed)} todo={len(todo)}", flush=True)
    chunk = 15
    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(m.load_symbol, s, "5m", True): s for s in batch}
            for fut in cf.as_completed(futs):
                s = futs[fut]
                try:
                    df = fut.result()
                    events.extend(m.extract_events(s, df))
                except Exception as e:
                    print("err", s, repr(e), flush=True)
        processed |= set(batch)
        pd.DataFrame(events).to_csv(ev_path, index=False)
        pf.write_text("\n".join(sorted(processed)))
        print(f"5m processed={len(processed)} events={len(events)} last_batch={batch[:3]}...", flush=True)
    print("5m chunk done", flush=True)


def phase_screen(limit=80, workers=10):
    import concurrent.futures as cf
    symbols = m.list_symbols()
    sdf = load_screen()
    done = set(sdf.symbol.astype(str)) if not sdf.empty else set()
    todo = [s for s in symbols if s not in done][:limit]
    print(f"screen done={len(done)} doing={len(todo)}", flush=True)
    rows = sdf.to_dict("records") if not sdf.empty else []
    n = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(m.screen_1h, s) for s in todo]
        for fut in cf.as_completed(futs):
            n += 1
            try:
                sym, res = fut.result()
                if res:
                    rows.append(res)
            except Exception as e:
                print("screen err", repr(e), flush=True)
            if n % 20 == 0:
                mid = pd.DataFrame(rows).drop_duplicates("symbol")
                mid.to_csv(OUT / "symbol_screen.partial.csv", index=False)
                print(f"screen mid={len(mid)} n={n}", flush=True)
    out = pd.DataFrame(rows).drop_duplicates("symbol")
    out.to_csv(OUT / "symbol_screen.partial.csv", index=False)
    print(f"screen now={len(out)} eligible={int(out.eligible.astype(str).str.lower().eq('true').sum())}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["screen", "5m"], required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    if args.phase == "screen":
        phase_screen(limit=args.limit or 80, workers=args.workers)
    else:
        phase_5m(limit=args.limit, workers=args.workers)
