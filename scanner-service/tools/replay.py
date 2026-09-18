#!/usr/bin/env python3
"""Replay real historical 1-minute klines through the LIVE engine.

This sandbox cannot open a websocket to Binance, so this is the substitute for
watching the scanner run: each archived 1-minute bar is expanded into synthetic
ticker snapshots on the engine's own slot grid (cumulative 24h quote volume,
last price, high/low), exactly the shape ``!ticker@arr`` delivers, and pushed
through ``Engine.on_ticker_batch``. The detection code path exercised is the
real one — no stub, no reimplementation.

    python3 tools/replay.py GENIUSUSDT 2026-09-14 2026-09-17
    python3 tools/replay.py KSMUSDT 2026-09-06 2026-09-17 --market futures

Interpolating within a bar cannot invent information, so the replay is a
faithful test of *plumbing and logic*, and a conservative test of timing: live
ticks arrive ~1/s and would trip the detectors marginally earlier.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys
import urllib.request
import zipfile
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from volscan.config import Config
from volscan.engine import Engine, Tick

HOST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".klines")


def fetch_day(symbol: str, day: str, market: str = "spot") -> Optional[List[list]]:
    key = (f"data/spot/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip" if market == "spot"
           else f"data/futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip")
    path = os.path.join(CACHE, key.replace("/", "_"))
    os.makedirs(CACHE, exist_ok=True)
    if os.path.exists(path):
        blob = open(path, "rb").read()
        if not blob:
            return None
    else:
        try:
            with urllib.request.urlopen(HOST + key, timeout=120) as r:
                blob = r.read()
            open(path, "wb").write(blob)
        except Exception:
            open(path, "wb").close()
            return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(z.namelist()[0]).decode()
    rows = []
    for line in raw.strip().split("\n"):
        if not line or line.startswith("open_time"):
            continue
        c = line.split(",")
        t = int(c[0])
        if t > 1e15:                       # newer archives are in microseconds
            t //= 1000
        rows.append([t, float(c[1]), float(c[2]), float(c[3]), float(c[4]),
                     float(c[7]), float(c[8]), float(c[10])])
    return rows


def days_between(a: str, b: str) -> List[str]:
    d0 = dt.date.fromisoformat(a)
    d1 = dt.date.fromisoformat(b)
    out = []
    while d0 <= d1:
        out.append(d0.isoformat())
        d0 += dt.timedelta(days=1)
    return out


def replay(symbol: str, start: str, end: str, market: str = "spot",
           cfg: Optional[Config] = None, verbose: bool = True):
    cfg = cfg or Config()
    cfg.min_base_age_sec = 0.0
    engine = Engine(cfg)
    mkt = "SPOT" if market == "spot" else "USDT-M"

    bars: List[list] = []
    for day in days_between(start, end):
        rows = fetch_day(symbol, day, market)
        if rows:
            bars += rows
    if not bars:
        print(f"{symbol}: нет данных в архиве за {start}..{end}")
        return engine, []
    bars.sort()

    # Walk each 1m bar as open -> extreme -> other extreme -> close. A straight
    # open->close interpolation never hands the engine the bar's high or low, so
    # the single-candle detector (which is all about a candle's own range) would
    # never see the range it is built to detect. The o/h/l/c path is the standard
    # conservative approximation: it visits the real extremes without inventing
    # any price the bar did not actually trade at.
    slot = cfg.accel_slot_sec
    per_bar = max(int(60 // slot), 1) * 2
    cum = 0.0
    ntr_cum = 0.0
    window24 = []                          # rolling 24h of (ts, quote_volume)
    signals = []
    for t, o, hi, lo, cl, qv, ntr, tbq in bars:
        cum += qv
        ntr_cum += ntr
        window24.append((t, qv))
        while window24 and t - window24[0][0] > 24 * 3600 * 1000:
            window24.pop(0)
        vol24 = sum(v for _, v in window24)
        # up bar: o -> l -> h -> c;  down bar: o -> h -> l -> c
        path = [o, lo, hi, cl] if cl >= o else [o, hi, lo, cl]
        step = 60_000.0 / per_bar
        for k in range(per_bar):
            frac = (k + 1) / per_bar
            ts = int(t + step * k)
            price = path[min(int(k * len(path) / per_bar), len(path) - 1)]
            tick = Tick(symbol=symbol, price=price, pct24=0.0,
                        quote_volume=cum - qv * (1 - frac),
                        trades=ntr_cum - ntr * (1 - frac),
                        high=hi, low=lo, vwap=(hi + lo + cl) / 3)
            tick.quote_volume = _as_24h(tick.quote_volume, cum, vol24)
            sigs = engine.on_ticker_batch(mkt, [tick], ts)
            for s in sigs:
                signals.append(s)
                if verbose and s.alert:
                    print(f"{dt.datetime.utcfromtimestamp(s.ts/1000):%Y-%m-%d %H:%M:%S} UTC  "
                          f"{s.kind:<9} score {s.score:>3}  {s.price:.6g}  "
                          f"{' | '.join(s.reasons[:2])}")
        if tbq > 0 and qv > 0:
            engine.on_kline(mkt, symbol, tbq, qv, int(t + 59_000))
    return engine, signals


def _as_24h(partial_cum: float, cum: float, vol24: float) -> float:
    """The live ticker reports a rolling 24h quote volume, not an all-time sum."""
    return max(vol24 - (cum - partial_cum), 1.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--market", default="spot", choices=("spot", "futures"))
    args = ap.parse_args()
    engine, sigs = replay(args.symbol, args.start, args.end, args.market)
    kinds = {}
    for s in sigs:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    print("\nитого сигналов:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())) or "нет")
    print("телеграм-алертов:", sum(1 for s in sigs if s.alert))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
