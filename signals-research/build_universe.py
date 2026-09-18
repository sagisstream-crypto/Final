"""Pick the coin basket: Binance spot USDT alts whose typical 24h quote volume
sits in the band the scanner targets (small -> upper-mid cap), alive for the
whole study window."""
import json, sys, statistics
from concurrent.futures import ThreadPoolExecutor
from binance_data import read_klines

PROBE_MONTHS = ["2025-09", "2026-02", "2026-08"]
STABLE = {"USDC","FDUSD","TUSD","BUSD","DAI","USDP","EURI","EUR","AEUR","XUSD","USD1","PYUSD","BFUSD"}
MAJORS = {"BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT"}

def base(sym):
    return sym[:-4]

def probe(sym):
    vols = {}
    for ym in PROBE_MONTHS:
        rows = read_klines(sym, "1d", ym)
        if not rows:
            return None
        qv = [float(r[7]) for r in rows]
        if len(qv) < 20:
            return None
        vols[ym] = statistics.median(qv)
    return sym, vols

def main():
    syms = json.load(open("cache/all_usdt_symbols.json"))
    syms = [s for s in syms if base(s) not in STABLE and s not in MAJORS]
    out = {}
    with ThreadPoolExecutor(max_workers=24) as ex:
        for res in ex.map(probe, syms):
            if res:
                out[res[0]] = res[1]
    json.dump(out, open("cache/universe_volumes.json","w"), indent=0)
    print("alive all window:", len(out))

if __name__ == "__main__":
    main()
