"""The long-lived service: wires feed -> engine -> storage/alerts/dashboard.

Everything is one asyncio process. State lives in SQLite, so a restart (or a
crash, or a reboot) resumes with the tracked pairs, the signal journal and the
outcome tracking intact.
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal as os_signal
import time
from typing import Dict, List, Optional

import aiohttp

from .alerts import Telegram
from .config import Config, coerce_setting
from .dashboard import Dashboard
from .engine import Engine, Tick
from .feed import HotKlineFeed, HourlyRefresher, MarketFeed
from .storage import Storage

log = logging.getLogger("volscan")

CHECKPOINTS = ((5, "p5"), (15, "p15"), (30, "p30"), (60, "p60"), (240, "p240"))


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.storage = Storage(cfg.db_path)
        self.engine = Engine(cfg)
        self.session: Optional[aiohttp.ClientSession] = None
        self.telegram: Optional[Telegram] = None
        self.dashboard: Optional[Dashboard] = None
        self.feeds: List[MarketFeed] = []
        self.hot: Optional[HotKlineFeed] = None
        self.hourly: Optional[HourlyRefresher] = None
        self._alert_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._stop = asyncio.Event()
        self._last_sample: Dict[str, int] = {}
        self.started_at = int(time.time() * 1000)

    # ------------------------------------------------------------- lifecycle
    async def run(self) -> None:
        self._restore()
        self._apply_stored_settings()
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)
        self.session = aiohttp.ClientSession(timeout=timeout)
        self.telegram = Telegram(self.cfg, lambda: self.session)
        tasks = []
        try:
            for market in self.cfg.markets:
                feed = MarketFeed(market, self.cfg, self.session, self._on_batch)
                self.feeds.append(feed)
                tasks.append(asyncio.create_task(feed.run(), name=f"feed-{market}"))
            self.hot = HotKlineFeed(self.cfg, self.session, self.engine)
            tasks.append(asyncio.create_task(self.hot.run(), name="hot"))
            self.hourly = HourlyRefresher(self.cfg, self.session, self.engine)
            tasks.append(asyncio.create_task(self.hourly.run(), name="hourly"))
            tasks.append(asyncio.create_task(self._flusher(), name="flusher"))
            tasks.append(asyncio.create_task(self._alerter(), name="alerter"))
            tasks.append(asyncio.create_task(self._outcomes(), name="outcomes"))
            if self.cfg.dashboard_enabled:
                self.dashboard = Dashboard(self.cfg, self.storage, self.status,
                                           on_settings=self._on_settings_changed)
                await self.dashboard.start()
                log.info("dashboard on http://%s:%s", self.cfg.dashboard_host,
                         self.cfg.dashboard_port)
            if self.telegram.configured:
                await self.telegram.send(
                    "✅ VolScan сервис запущен\n"
                    "Сигналы: ⚡ РАННИЙ ПРОБОЙ → 🚀 УСКОРЕНИЕ → 🔥 АНОМАЛЬНЫЙ ВЫНОС")
            await self._stop.wait()
        finally:
            for f in self.feeds:
                f.stop()
            if self.hot:
                self.hot.stop()
            if self.hourly:
                self.hourly.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.dashboard:
                await self.dashboard.stop()
            if self.session:
                await self.session.close()
            self.storage.close()
            log.info("stopped cleanly")

    def request_stop(self, *_):
        log.info("shutdown requested")
        self._stop.set()

    def install_signal_handlers(self, loop) -> None:
        for s in (os_signal.SIGINT, os_signal.SIGTERM):
            try:
                loop.add_signal_handler(s, self.request_stop)
            except NotImplementedError:      # Windows
                os_signal.signal(s, self.request_stop)

    # -------------------------------------------------------------- settings
    def _apply_stored_settings(self) -> None:
        """Settings edited from the dashboard outlive a restart: they are stored
        in SQLite and layered on top of config.json here."""
        try:
            stored = self.storage.load_settings()
        except Exception as exc:
            log.warning("could not read stored settings: %r", exc)
            return
        applied = 0
        for name, raw in stored.items():
            try:
                value = coerce_setting(name, json.loads(raw))
            except Exception:
                continue
            setattr(self.cfg, name, value)
            applied += 1
        if applied:
            log.info("applied %d setting(s) saved from the dashboard", applied)

    def _on_settings_changed(self, changed: dict) -> None:
        names = ", ".join(sorted(changed))
        log.info("settings changed from the dashboard: %s", names)

    # --------------------------------------------------------------- restore
    def _restore(self) -> None:
        rows = self.storage.load_pairs()
        for r in rows:
            key = r["key"]
            from .engine import SymbolState
            st = SymbolState(key, r["market"], r["symbol"], r["base"] or "",
                             self.cfg.quote, self.cfg)
            st.session_base = r["session_base"]
            st.base_time = r["base_time"] or 0
            st.price_base = r["price_base"]
            st.day_high = r["day_high"]
            st.day_high_at = r["day_high_at"] or 0
            st.day_low = r["day_low"]
            st.cluster_at = r["cluster_at"] or 0
            st.signal_count = r["signal_count"] or 0
            self.engine.states[key] = st
        if rows:
            log.info("restored %d tracked pairs from %s", len(rows), self.cfg.db_path)

    # ----------------------------------------------------------------- feed
    def _on_batch(self, market: str, ticks: List[Tick], now: int, resync: bool) -> None:
        signals = self.engine.on_ticker_batch(market, ticks, now, resync)
        for sig in signals:
            self.storage.queue_signal(sig, sig.alert)
            if sig.alert:
                try:
                    self._alert_queue.put_nowait(sig)
                except asyncio.QueueFull:
                    log.warning("alert queue full, dropping %s %s", sig.kind, sig.symbol)
        # persist a downsampled snapshot per pair for the drill-down chart
        for st in self.engine.states.values():
            if st.market != market or not st.snapshot:
                continue
            last = self._last_sample.get(st.key, 0)
            if now - last < 30_000:
                continue
            self._last_sample[st.key] = now
            s = st.snapshot
            self.storage.queue_sample(st.key, now, s.get("price"), s.get("day_qv"),
                                      s.get("d1m"), s.get("rvol"), s.get("score"),
                                      s.get("accel"))
            self.storage.queue_pair(st)
            self.storage.queue_live(st.key, now, _live_payload(st))

    # ----------------------------------------------------------- background
    async def _flusher(self) -> None:
        last_prune = 0
        while not self._stop.is_set():
            await asyncio.sleep(self.cfg.flush_sec)
            try:
                n = self.storage.flush()
                if n:
                    log.debug("flushed %d rows", n)
                now = time.time()
                if now - last_prune > 3600:
                    last_prune = now
                    self.storage.prune()
                    self.storage.close_stale()
            except Exception as exc:
                log.error("flush failed: %r", exc)

    async def _alerter(self) -> None:
        while not self._stop.is_set():
            sig = await self._alert_queue.get()
            try:
                await self.telegram.send_signal(sig)
            except Exception as exc:
                log.error("alert failed: %r", exc)

    async def _outcomes(self) -> None:
        """Fill in what happened after each signal — 5/15/30/60/240 min prices
        plus running MFE/MAE. This is the feedback loop that lets the hit rates
        in SIGNALS.md be re-measured on the user's own live data."""
        while not self._stop.is_set():
            await asyncio.sleep(30)
            try:
                now = int(time.time() * 1000)
                updates = []
                for row in self.storage.open_signals():
                    st = self.engine.states.get(row["key"])
                    if st is None or not st.snapshot:
                        continue
                    price = st.snapshot.get("price")
                    if not price or not row["price"]:
                        continue
                    chg = (price - row["price"]) / row["price"] * 100
                    elapsed = (now - row["ts"]) / 60000.0
                    mfe, mfe_min = row["mfe"] or 0.0, row["mfe_min"] or 0.0
                    mae, mae_min = row["mae"] or 0.0, row["mae_min"] or 0.0
                    if chg > mfe:
                        mfe, mfe_min = chg, elapsed
                    if chg < mae:
                        mae, mae_min = chg, elapsed
                    vals = {}
                    for minutes, field in CHECKPOINTS:
                        cur = row[field]
                        vals[field] = cur if cur is not None else (price if elapsed >= minutes else None)
                    done = 1 if vals["p240"] is not None else 0
                    updates.append((vals["p5"], vals["p15"], vals["p30"], vals["p60"],
                                    vals["p240"], mfe, mfe_min, mae, mae_min, done, row["id"]))
                if updates:
                    self.storage.update_outcomes(updates)
            except Exception as exc:
                log.error("outcome update failed: %r", exc)

    # --------------------------------------------------------------- status
    def status(self) -> dict:
        parts = [f"{f.market}:{f.status.upper()[:4]}"
                 f"{'·REST' if f.mode == 'rest' else ''} msg {f.msgs}"
                 f"{' err ' + str(f.errors) if f.errors else ''}"
                 for f in self.feeds]
        parts.append(f"пар {len(self.engine.states)}")
        if self.hourly:
            parts.append(f"1ч-контекст {self.hourly.updated}")
        return {"status": " · ".join(parts),
                "telegram": bool(self.telegram and self.telegram.configured)}


def _live_payload(st) -> dict:
    s = st.snapshot
    keep = ("price", "score", "rvol", "d1m", "d2m", "pct1m", "pct2m", "pct5m", "taker",
            "taker_w", "net_flow_x", "mult", "day_qv", "dist_high24", "range15",
            "range15_rel", "session_gain", "vol_z", "accel_gain", "accel_r2_p",
            "accel_r2_v", "blast_trades_x", "blast_range", "blast_range_x",
            "blast_rsi6", "hourly_rsi", "hourly_bb_pct", "hourly_vol_z")
    out = {"key": st.key, "symbol": st.symbol, "base": st.base, "market": st.market,
           "ts": s.get("ts")}
    for k in keep:
        v = s.get(k)
        if isinstance(v, (int, float)):
            out[k] = round(float(v), 8)
    for k in ("accel", "blast", "candidate", "accumulating", "compressed",
              "break15", "precursor", "background_ok"):
        if s.get(k):
            out[k] = True
    out["reasons"] = " · ".join(s.get("reasons", [])[:5])
    return out
