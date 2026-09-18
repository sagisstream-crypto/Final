"""Telegram alerting.

Two rules govern everything here:

1. **Every message names its signal type in plain Russian on the first line**,
   with a one-line explanation of what that pattern actually means. You should
   be able to glance at the phone and know "это накопление" vs "это пробой"
   without reading a single number.
2. **One message per real event.** The engine already collapses a pump into a
   single escalating episode; this module adds a global rate limit and a
   per-pair floor so a burst of pairs during a market-wide move cannot turn
   into a firehose.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Optional

from .config import Config
from .indicators import fmt_money

log = logging.getLogger("volscan.alerts")

# type -> (header, one-line plain-Russian explanation)
TYPES = {
    "EARLY":     ("⚡ РАННИЙ ПРОБОЙ",
                  "объём и цена только начали расти — вход на раннем этапе"),
    "ACCEL":     ("🚀 УСКОРЕНИЕ",
                  "цена И объём растут непрерывно несколько минут подряд, а не одним всплеском"),
    "BLOWOFF":   ("🔥 АНОМАЛЬНЫЙ ВЫНОС — УСКОРЯЕТСЯ",
                  "рост продолжает ускоряться после сигнала, откатов нет"),
    "CANDIDATE": ("🎯 КАНДИДАТ НА +10%",
                  "сетап, который на истории в 18.9% случаев давал +10% за 4 часа"),
    "ANOMALY":   ("🔥 АНОМАЛЬНЫЙ ОБЪЁМ",
                  "минутный оборот резко выбился из собственной нормы пары"),
    "ACCUM":     ("🐋 НАКОПЛЕНИЕ",
                  "цена стоит в узком диапазоне, но покупатели давят много минут подряд"),
    "WAKE":      ("😴→⚡ ВЫХОД ИЗ СЖАТИЯ",
                  "пара стояла в узком коридоре и начала выходить вверх"),
    "SCORE":     ("⭐ ВЫСОКИЙ SCORE",
                  "составная оценка выше порога"),
}


class Telegram:
    def __init__(self, cfg: Config, session_factory):
        self.cfg = cfg
        self._session_factory = session_factory
        self._sent = deque(maxlen=400)       # timestamps, for the global rate limit
        self.sent_count = 0
        self.dropped_count = 0

    @property
    def configured(self) -> bool:
        return bool(self.cfg.telegram_token and self.cfg.telegram_chat_id)

    def _rate_ok(self, now_ms: int) -> bool:
        hour = now_ms - 3600_000
        recent = sum(1 for t in self._sent if t >= hour)
        return recent < 30                   # hard ceiling: 30 messages per hour

    def allowed(self, kind: str) -> bool:
        if kind in ("EARLY", "ACCEL", "BLOWOFF"):
            return True
        if kind == "CANDIDATE":
            return self.cfg.alert_candidate
        if kind == "ACCUM":
            return self.cfg.alert_accumulation
        if kind == "ANOMALY":
            return self.cfg.alert_anomaly
        return False

    async def send_signal(self, sig) -> bool:
        if not self.configured or not self.allowed(sig.kind):
            return False
        now = int(time.time() * 1000)
        if not self._rate_ok(now):
            self.dropped_count += 1
            log.warning("telegram rate limit hit, dropping %s %s", sig.kind, sig.symbol)
            return False
        self._sent.append(now)
        await self.send(format_signal(sig, self.cfg))
        self.sent_count += 1
        return True

    async def send(self, text: str) -> None:
        if not self.configured:
            return
        url = f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage"
        payload = {"chat_id": self.cfg.telegram_chat_id, "text": text,
                   "disable_web_page_preview": True}
        for attempt in range(3):
            try:
                session = self._session_factory()
                async with session.post(url, json=payload, timeout=20) as r:
                    if r.status == 200:
                        return
                    body = await r.text()
                    log.warning("telegram HTTP %s: %s", r.status, body[:200])
            except Exception as exc:          # network hiccups must never kill the scanner
                log.warning("telegram send failed (%s/3): %r", attempt + 1, exc)
            await asyncio.sleep(2 * (attempt + 1))


def format_signal(sig, cfg: Config) -> str:
    header, explain = TYPES.get(sig.kind, ("СИГНАЛ", ""))
    f = sig.features or {}
    lines = [
        header,
        explain,
        "",
        f"{sig.symbol} · {sig.market} · score {sig.score}",
        f"цена {_price(sig.price)}",
    ]
    bits = []
    if f.get("rvol") is not None:
        bits.append(f"RVOL ×{f['rvol']:.0f}")
    if f.get("d1m") is not None:
        bits.append(f"Δ1м ${fmt_money(f['d1m'])}")
    if f.get("pct1m") is not None:
        bits.append(f"{f['pct1m']:+.2f}%/1м")
    if f.get("pct5m") is not None:
        bits.append(f"{f['pct5m']:+.2f}%/5м")
    if bits:
        lines.append(" · ".join(bits))
    bits2 = []
    if f.get("taker") is not None:
        bits2.append(f"тейкер {f['taker']*100:.0f}% buy")
    if f.get("mult") is not None:
        bits2.append(f"×{f['mult']:.2f} от базы сессии")
    if f.get("dist_high24") is not None:
        bits2.append(f"до 24ч хая {f['dist_high24']:+.1f}%")
    if bits2:
        lines.append(" · ".join(bits2))
    if sig.reasons:
        lines.append("")
        lines.append(" · ".join(sig.reasons[:5]))
    if sig.kind in ("ACCUM", "WAKE"):
        lines.append("")
        lines.append("⚠️ контекст, не сигнал на вход: на истории у этого паттерна "
                     "нет преимущества над случайным входом (см. SIGNALS.md)")
    if cfg.dashboard_enabled:
        lines.append("")
        lines.append(f"график: /pair/{sig.key}")
    return "\n".join(lines)


def _price(p: Optional[float]) -> str:
    if p is None:
        return "—"
    return f"{p:.6g}"
