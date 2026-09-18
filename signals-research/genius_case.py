"""Hand-verified ground-truth case: the GENIUS/USDT pump the user's phone
alerted on. Pulls the real 1m archive for that day and replays the acceleration
detector over it minute by minute."""
import io, sys, zipfile, urllib.request, datetime as dt
import numpy as np
import features as F, accel as A
from binance_data import HOST, fetch_zip

def daily(sym, day, market="spot"):
    key = (f"data/spot/daily/klines/{sym}/1m/{sym}-1m-{day}.zip" if market == "spot"
           else f"data/futures/um/daily/klines/{sym}/1m/{sym}-1m-{day}.zip")
    blob = fetch_zip(key)
    if blob is None:
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(z.namelist()[0]).decode()
    rows = [r.split(",") for r in raw.strip().split("\n") if r and not r.startswith("open_time")]
    out = []
    for r in rows:
        t = int(r[0])
        if t > 1e15:
            t //= 1000
        out.append((t, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                    float(r[7]), float(r[8]), float(r[10])))
    return out


def load_days(sym, days, market="spot"):
    rows = []
    for d in days:
        r = daily(sym, d, market)
        if r:
            rows += r
    if not rows:
        return None
    rows.sort()
    a = np.array(rows, dtype=float)
    return dict(ts=a[:, 0].astype(np.int64), open=a[:, 1], high=a[:, 2], low=a[:, 3],
                close=a[:, 4], qv=a[:, 5], trades=a[:, 6], tbq=a[:, 7])


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "GENIUSUSDT"
    market = sys.argv[2] if len(sys.argv) > 2 else "spot"
    days = [f"2026-09-{d:02d}" for d in range(14, 18)]
    d = load_days(sym, days, market)
    if d is None:
        print("no data"); return
    print(f"{sym} {market}: {len(d['ts'])} bars "
          f"{dt.datetime.utcfromtimestamp(d['ts'][0]/1000)} .. "
          f"{dt.datetime.utcfromtimestamp(d['ts'][-1]/1000)} UTC")
    f = F.build(d)
    st = A.accel_state(d["close"], d["qv"], f["avg_min"], w=10, min_gain=2.0, min_rvol=12.0)
    fire = A.persist(st["state"], 2)
    # biggest 1m candle of the last day
    last = d["ts"] >= d["ts"][-1] - 24 * 3600 * 1000
    i_big = int(np.nanargmax(np.where(last, np.nan_to_num(f["pct1m"], nan=-99), -99)))
    t_big = dt.datetime.utcfromtimestamp(d["ts"][i_big] / 1000)
    print(f"\nbiggest 1m candle of the final day: {t_big} UTC  {f['pct1m'][i_big]:+.2f}%  "
          f"RVOL x{f['rvol'][i_big]:.0f}  qv ${d['qv'][i_big]:,.0f}")
    lo, hi = i_big - 30, i_big + 8
    print(f"\n{'UTC':<9}{'close':>12}{'Δ%1m':>8}{'RVOL':>8}{'Δ1м $':>12}{'taker':>7}"
          f"{'r2p':>7}{'r2v':>7}{'gain10':>8}  flags")
    first_fire = None
    for i in range(max(lo, 0), min(hi, len(d["ts"]))):
        tt = dt.datetime.utcfromtimestamp(d["ts"][i] / 1000).strftime("%H:%M:%S")
        tk = d["tbq"][i] / d["qv"][i] if d["qv"][i] > 0 else float("nan")
        flags = []
        if st["state"][i]:
            flags.append("accel-state")
        if fire[i]:
            flags.append("🚀 FIRE")
            if first_fire is None:
                first_fire = i
        print(f"{tt:<9}{d['close'][i]:>12.6f}{f['pct1m'][i]:>8.2f}{f['rvol'][i]:>8.1f}"
              f"{d['qv'][i]:>12,.0f}{tk:>7.2f}"
              f"{_n(st['r2_p'][i]):>7}{_n(st['r2_v'][i]):>7}{_n(st['gain'][i]):>8}  {' '.join(flags)}")
    if first_fire is not None:
        lead = (d["ts"][i_big] - d["ts"][first_fire]) / 60000
        print(f"\nfirst 🚀 fire at {dt.datetime.utcfromtimestamp(d['ts'][first_fire]/1000)} UTC — "
              f"{lead:.0f} min before the explosive candle; "
              f"price then {d['close'][first_fire]:.6f} -> peak next 4h "
              f"{np.nanmax(d['high'][first_fire:first_fire+240]):.6f} "
              f"({100*(np.nanmax(d['high'][first_fire:first_fire+240])/d['close'][first_fire]-1):+.1f}%)")
    else:
        print("\nno acceleration fire in this window")


def _n(v):
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.2f}"


if __name__ == "__main__":
    main()
