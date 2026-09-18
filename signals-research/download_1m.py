"""Warm the local cache with 1m monthly kline archives for the whole basket."""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from binance_data import fetch_zip, monthly_key, months

START, END = "2025-09", "2026-08"

def job(args):
    sym, ym = args
    try:
        b = fetch_zip(monthly_key(sym, "1m", ym))
        return (sym, ym, 0 if b is None else len(b), None)
    except Exception as e:
        return (sym, ym, -1, repr(e))

def main():
    basket = [x["symbol"] for x in json.load(open("cache/basket.json"))]
    tasks = [(s, m) for s in basket for m in months(START, END)]
    t0 = time.time()
    done = miss = fail = 0
    tot = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for sym, ym, n, err in ex.map(job, tasks):
            if n < 0:
                fail += 1; print("FAIL", sym, ym, err, flush=True)
            elif n == 0:
                miss += 1
            else:
                done += 1; tot += n
            if (done + miss + fail) % 60 == 0:
                print(f"{done+miss+fail}/{len(tasks)} ok={done} miss={miss} fail={fail} "
                      f"{tot/1e6:.0f}MB {time.time()-t0:.0f}s", flush=True)
    print(f"DONE ok={done} miss={miss} fail={fail} {tot/1e6:.0f}MB in {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
