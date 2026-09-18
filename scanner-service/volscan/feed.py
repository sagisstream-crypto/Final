"""Binance market data feed.

Same resilience model as the browser build, minus the browser:

* one websocket per market on ``!ticker@arr``;
* exponential backoff on disconnect (0.8s -> 15s);
* a watchdog: if the socket is OPEN but silent for ``ws_stale_sec`` we assume a
  half-dead connection and fall back to REST polling, then keep probing the
  websocket in the background and switch back the moment it delivers again;
* a second, small websocket on ``<sym>@kline_1m`` for the hottest pairs, which
  is where the taker-buy ratio comes from;
* a slow REST poller that refreshes the hourly background context (RSI/BB/vol_z)
  used by the score.

Nothing here knows about detection; it just hands normalised Ticks to a callback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Callable, Dict, List, Optional

import aiohttp

from .config import Config
from .engine import HourlyContext, Tick

log = logging.getLogger("volscan.feed")

MARKETS = {
    "SPOT": {
        "ws": "wss://stream.binance.com:9443/ws/!ticker@arr",
        "ws_stream": "wss://stream.binance.com:9443/stream?streams=",
        "rest": "https://api.binance.com/api/v3/ticker/24hr",
        "klines": "https://api.binance.com/api/v3/klines",
    },
    "USDT-M": {
        "ws": "wss://fstream.binance.com/ws/!ticker@arr",
        "ws_stream": "wss://fstream.binance.com/stream?streams=",
        "rest": "https://fapi.binance.com/fapi/v1/ticker/24hr",
        "klines": "https://fapi.binance.com/fapi/v1/klines",
    },
}


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def parse_ws_ticker(item: dict) -> Optional[Tick]:
    try:
        return Tick(symbol=item["s"], price=float(item["c"]), pct24=float(item.get("P", 0)),
                    quote_volume=float(item["q"]), trades=_num(item.get("n")),
                    bid_price=_num(item.get("b")), bid_qty=_num(item.get("B")),
                    ask_price=_num(item.get("a")), ask_qty=_num(item.get("A")),
                    vwap=_num(item.get("w")), high=_num(item.get("h")), low=_num(item.get("l")))
    except (KeyError, TypeError, ValueError):
        return None


def parse_rest_ticker(item: dict) -> Optional[Tick]:
    try:
        return Tick(symbol=item["symbol"], price=float(item["lastPrice"]),
                    pct24=float(item.get("priceChangePercent", 0)),
                    quote_volume=float(item["quoteVolume"]), trades=_num(item.get("count")),
                    bid_price=_num(item.get("bidPrice")), bid_qty=_num(item.get("bidQty")),
                    ask_price=_num(item.get("askPrice")), ask_qty=_num(item.get("askQty")),
                    vwap=_num(item.get("weightedAvgPrice")), high=_num(item.get("highPrice")),
                    low=_num(item.get("lowPrice")))
    except (KeyError, TypeError, ValueError):
        return None


class MarketFeed:
    """One market (SPOT or USDT-M) with websocket + REST fallback."""

    def __init__(self, market: str, cfg: Config, session: aiohttp.ClientSession,
                 on_batch: Callable[[str, List[Tick], int, bool], None]):
        self.market = market
        self.cfg = cfg
        self.session = session
        self.on_batch = on_batch
        self.urls = MARKETS[market]
        self.mode = "ws"
        self.status = "init"
        self.last_msg_at = 0
        self.msgs = 0
        self.errors = 0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _emit(self, ticks: List[Tick]) -> None:
        now = int(time.time() * 1000)
        gap = now - self.last_msg_at if self.last_msg_at else 0
        resync = gap > 15_000
        self.last_msg_at = now
        self.msgs += 1
        self.status = "ok"
        self.on_batch(self.market, ticks, now, resync)

    async def run(self) -> None:
        delay = self.cfg.ws_reconnect_base_sec
        while not self._stop.is_set():
            try:
                ok = await self._run_ws()
                delay = self.cfg.ws_reconnect_base_sec if ok else min(
                    delay * 2, self.cfg.ws_reconnect_max_sec)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.errors += 1
                self.status = "error"
                log.warning("%s websocket failed: %r", self.market, exc)
                delay = min(delay * 2, self.cfg.ws_reconnect_max_sec)
            if self._stop.is_set():
                break
            # while the websocket is down, keep the data flowing over REST
            await self._rest_for(delay)

    async def _run_ws(self) -> bool:
        got_any = False
        self.status = "connecting"
        async with self.session.ws_connect(self.urls["ws"], heartbeat=20,
                                           timeout=aiohttp.ClientWSTimeout(ws_close=20)) as ws:
            self.mode = "ws"
            log.info("%s websocket connected", self.market)
            while not self._stop.is_set():
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=self.cfg.ws_stale_sec)
                except asyncio.TimeoutError:
                    log.warning("%s websocket silent for %.0fs — switching to REST",
                                self.market, self.cfg.ws_stale_sec)
                    return got_any
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR):
                    return got_any
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    arr = json.loads(msg.data)
                except ValueError:
                    continue
                if not isinstance(arr, list):
                    continue
                ticks = [t for t in (parse_ws_ticker(i) for i in arr) if t]
                if ticks:
                    got_any = True
                    self._emit(ticks)
        return got_any

    async def _rest_for(self, seconds: float) -> None:
        """Poll the REST snapshot while we wait to retry the websocket."""
        self.mode = "rest"
        deadline = time.time() + max(seconds, self.cfg.rest_poll_sec)
        while time.time() < deadline and not self._stop.is_set():
            try:
                async with self.session.get(self.urls["rest"], timeout=20) as r:
                    r.raise_for_status()
                    arr = await r.json()
                ticks = [t for t in (parse_rest_ticker(i) for i in arr) if t]
                if ticks:
                    self._emit(ticks)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.errors += 1
                self.status = "error"
                log.warning("%s REST poll failed: %r", self.market, exc)
            await asyncio.sleep(self.cfg.rest_poll_sec)


class HotKlineFeed:
    """kline_1m for the hottest N spot symbols — the taker-buy ratio source."""

    def __init__(self, cfg: Config, session: aiohttp.ClientSession, engine):
        self.cfg = cfg
        self.session = session
        self.engine = engine
        self.symbols: List[str] = []
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _pick(self) -> List[str]:
        rows = [s for s in self.engine.states.values()
                if s.market == "SPOT" and s.snapshot.get("d1m")]
        rows.sort(key=lambda s: (-(s.snapshot.get("score") or 0),
                                 -(s.snapshot.get("d1m") or 0)))
        return [s.symbol.lower() for s in rows[: self.cfg.hot_symbols]]

    async def run(self) -> None:
        while not self._stop.is_set():
            syms = self._pick()
            if not syms:
                await asyncio.sleep(10)
                continue
            url = MARKETS["SPOT"]["ws_stream"] + "/".join(f"{s}@kline_1m" for s in syms)
            self.symbols = syms
            try:
                async with self.session.ws_connect(url, heartbeat=20) as ws:
                    deadline = time.time() + self.cfg.hot_rebuild_sec
                    while time.time() < deadline and not self._stop.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=10)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type is not aiohttp.WSMsgType.TEXT:
                            break
                        try:
                            k = json.loads(msg.data)["data"]["k"]
                            vol, buy = float(k["q"]), float(k["Q"])
                        except (KeyError, ValueError, TypeError):
                            continue
                        if vol > 0:
                            self.engine.on_kline("SPOT", k["s"], buy, vol,
                                                 int(time.time() * 1000))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("hot kline stream failed: %r", exc)
                await asyncio.sleep(5)


# --------------------------------------------------------------------------- #
#  hourly background context (the sibling backtest's precursor features)
# --------------------------------------------------------------------------- #
def rsi(closes: List[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def bb_width_series(closes: List[float], n: int = 24) -> List[float]:
    out = []
    for i in range(n, len(closes) + 1):
        w = closes[i - n:i]
        m = sum(w) / n
        sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
        out.append((4 * sd / m * 100) if m else 0.0)
    return out


def hourly_context(klines: List[list], now_ms: int) -> HourlyContext:
    """klines: Binance 1h rows, oldest first. Only closed bars are used."""
    if len(klines) < 60:
        return HourlyContext(updated_at=now_ms)
    rows = klines[:-1]                      # drop the still-open bar
    closes = [float(r[4]) for r in rows]
    vols = [float(r[7]) for r in rows]
    widths = bb_width_series(closes)
    ctx = HourlyContext(updated_at=now_ms)
    ctx.rsi14 = rsi(closes[-100:], 14)
    if widths:
        cur = widths[-1]
        hist = widths[-720:] if len(widths) > 1 else widths
        ctx.bb_width_pct = sum(1 for w in hist if w <= cur) / len(hist)
    base = vols[-169:-1]
    if len(base) >= 24:
        m = sum(base) / len(base)
        sd = (sum((v - m) ** 2 for v in base) / len(base)) ** 0.5
        ctx.vol_z_1h = ((vols[-1] - m) / sd) if sd > 0 else None
    if len(closes) >= 5:
        ctx.ret_4h = (closes[-1] / closes[-5] - 1) * 100
    trs = []
    for i in range(1, min(len(rows), 25)):
        h, l, pc = float(rows[-i][2]), float(rows[-i][3]), float(rows[-i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if trs and closes[-1]:
        ctx.atr_pct = sum(trs) / len(trs) / closes[-1] * 100
    return ctx


class HourlyRefresher:
    """Slowly walks the tracked pairs refreshing their 1h background context."""

    def __init__(self, cfg: Config, session: aiohttp.ClientSession, engine):
        self.cfg = cfg
        self.session = session
        self.engine = engine
        self._stop = asyncio.Event()
        self.updated = 0

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            keys = sorted(self.engine.states.keys(),
                          key=lambda k: self.engine.states[k].hourly.updated_at)[:40]
            for key in keys:
                if self._stop.is_set():
                    return
                st = self.engine.states.get(key)
                if st is None:
                    continue
                url = MARKETS[st.market]["klines"]
                try:
                    async with self.session.get(
                            url, params={"symbol": st.symbol, "interval": "1h",
                                         "limit": "200"}, timeout=20) as r:
                        if r.status != 200:
                            continue
                        rows = await r.json()
                    st.hourly = hourly_context(rows, int(time.time() * 1000))
                    self.updated += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.debug("hourly refresh failed for %s: %r", key, exc)
                await asyncio.sleep(0.4)      # stay well inside the REST weight limits
            await asyncio.sleep(30)
