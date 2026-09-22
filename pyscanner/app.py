#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ShelfScan (Python) — сканер «тихая полка → вынос» для USDT-M фьючерсов Binance.

Архитектура (всё в одном процессе, asyncio):
  1. REST: список пар (exchangeInfo), 24ч обороты (ticker/24hr), история
     (klines 15m + klines 1d на пару) — с троттлером веса, как в JS-версии.
  2. WebSocket к Binance:
       - !ticker@arr           — живой оборот 24ч и цена по всем парам разом
       - {sym}@kline_15m       — 15-минутные бары: полка считается на закрытии,
                                  вынос может поймать ещё на форминг-баре
                                  (Binance шлёт обновления форминг-бара ~раз/сек)
       - {sym}@kline_1m        — НАСТОЯЩИЕ закрытые минутные свечи. Используются
                                  только как диагностика (как именно набирался
                                  объём внутри бара), НЕ как вход в модель —
                                  проверено на 1551 сигнале: добавление этих
                                  признаков в модель ухудшает AUC (0.849→0.815,
                                  переобучение на малой выборке), см. README.
  3. Признаки и модель — pyscanner/model.py, портировано из исследования
     построчно, счёт сверен с оригиналом до 3.4e-5 (см. README_PYTHON.md).
  4. Локальный веб-сервер (aiohttp) на localhost:8765 отдаёт тот же дашборд,
     что и shelf-scanner.html — вкладки Радар/Сигналы/Настройки/Модель,
     только браузер опрашивает не Binance напрямую, а локальный backend.
     Это даёт стабильность: скрипт не зависит от того, открыта ли вкладка
     браузера, может работать как фоновый процесс/службу.

Запуск: см. README_PYTHON.md в корне репозитория.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import statistics
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# Некоторые антивирусы (Kaspersky, ESET и т.п.) и корпоративные прокси
# подменяют TLS-сертификаты для проверки трафика («самоподписанный сертификат
# в цепочке»). Браузер доверяет такому сертификату автоматически — он лежит
# в системном хранилище Windows/macOS. Библиотеке aiohttp это хранилище
# не известно по умолчанию, поэтому без этой строчки запросы к Binance и
# Telegram падают с CERTIFICATE_VERIFY_FAILED, хотя тот же адрес прекрасно
# открывается в браузере. truststore перенаправляет проверку сертификатов
# в системное хранилище — ровно туда же, куда смотрит браузер.
try:
    import truststore
    truststore.inject_into_ssl()
    _TRUSTSTORE_OK = True
except Exception as _e:  # пакет не поставился или Python < 3.10 — не роняем сканер
    _TRUSTSTORE_OK = False
    print(f"[shelfscan] truststore не подключился ({_e}); если Telegram/Binance "
          f"падают с CERTIFICATE_VERIFY_FAILED — см. README_PYTHON.md", file=sys.stderr)

import aiohttp
from aiohttp import web

import model as M

# --------------------------------------------------------------------------- пути
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
SIGNALS_PATH = DATA_DIR / "signals.json"
LOG_PATH = DATA_DIR / "scanner.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
log = logging.getLogger("shelfscan")

# --------------------------------------------------------------------------- константы
API = "https://fapi.binance.com"
WS_BASE = "wss://fstream.binance.com"
HTTP_PORT = 8765
# Метка версии кода. Python-процесс не перечитывает файлы на лету — если
# после обновления app.py эта строка на дашборде («Диагностика») не
# совпадает с тем, что вы ожидаете увидеть, значит запущен СТАРЫЙ процесс:
# закройте окно консоли (или Ctrl+C) и запустите run_windows.bat заново.
BUILD = "2026-09-22.5-live-radar-tick-counter"
BARS_KEEP = 260          # закрытых 15м баров в кольцевом буфере на пару
M1_KEEP = 20             # закрытых 1м баров в буфере на пару (только для диагностики)
DAILY_LIMIT = 31
KLINE15_LIMIT = 220
CHUNK_SYMS = 150         # символов на один комбинированный WS-стрим
HIST_CONCURRENCY = 10    # параллельных REST-запросов истории


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- конфиг
def load_config() -> dict:
    cfg = dict(M.DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text("utf-8"))
            cfg.update({k: v for k, v in saved.items() if k in cfg})
        except Exception as e:
            log.warning("не смог прочитать config.json: %s", e)
    return cfg


def save_config(cfg: dict):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
    except Exception as e:
        log.warning("не смог сохранить config.json: %s", e)


CFG = load_config()

# --------------------------------------------------------------------------- состояние
S: dict[str, dict] = {}      # symbol -> state
BTC15: list = []             # ссылка на bars BTCUSDT, для признака режима рынка
SIGNALS: list[dict] = []     # последние сигналы, новые в начале
LOG_LINES: deque = deque(maxlen=300)
WATCHLIST: list[str] = []

DIAG = dict(bars=0, evals=0, max_rvol=0.0, max_rvol_sym="", last_bar_ts=0,
            started=now_ms(), near_miss=0, ws_ok15=0, ws_total15=0,
            ws_ok1m=0, ws_total1m=0, history_done=0, history_total=0, ready=False,
            ws_fail_streak=0, ws_last_error="", ws_last_error_ts=0, ws_ever_connected=False,
            # счётчик КАЖДОГО входящего kline-сообщения (15m и 1m) — честное
            # доказательство, что поток жив, отдельно от того, изменилось ли
            # что-то у конкретной пары прямо сейчас (см. восемнадцатую ловушку в JS)
            ws_msgs=0, last_msg_ts=0)


def push_log(msg: str, level: str = ""):
    LOG_LINES.appendleft(dict(t=now_ms(), msg=msg, level=level))
    log.info(msg)


def new_sym_state(sym: str, onboard: int) -> dict:
    return dict(
        sym=sym, onboard=onboard, qv24=0.0, price=0.0,
        bars=deque(maxlen=BARS_KEEP), daily=[], live=None, m1=deque(maxlen=M1_KEEP),
        score=0.0, ready=0.0, state="—", feats=None, cache=None,
        live_rvol=None, live_brk=None, live_ready=None,
        last_sig=0, last_eval=0, last_tick_at=0,
    )


# --------------------------------------------------------------------------- REST-троттлер
class Budget:
    """Держит вес запросов Binance ниже лимита (как в JS-версии)."""
    def __init__(self, limit_per_min=1000):
        self.limit = limit_per_min
        self.used = 0
        self.reset_at = time.time() + 60

    async def spend(self, w: int):
        while True:
            now = time.time()
            if now >= self.reset_at:
                self.used = 0
                self.reset_at = now + 60
            if self.used + w <= self.limit:
                self.used += w
                return
            await asyncio.sleep(max(0.2, self.reset_at - now))


BUDGET = Budget()


async def get_json(session: aiohttp.ClientSession, path: str, weight: int = 1, params=None):
    await BUDGET.spend(weight)
    async with session.get(API + path, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
        if r.status != 200:
            body = await r.text()
            raise RuntimeError(f"HTTP {r.status} {path} {body[:150]}")
        return await r.json()


def kline_rest_to_bar(k) -> dict:
    return dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]),
                qv=float(k[7]), n=float(k[8]), tb=float(k[10]))


def kline_ws_to_bar(k: dict) -> dict:
    return dict(t=int(k["t"]), o=float(k["o"]), h=float(k["h"]), l=float(k["l"]), c=float(k["c"]),
                qv=float(k["q"]), n=float(k["n"]), tb=float(k["Q"]))


# --------------------------------------------------------------------------- загрузка вотчлиста + истории
async def build_watchlist(session: aiohttp.ClientSession) -> list[str]:
    push_log("беру список пар…")
    info = await get_json(session, "/fapi/v1/exchangeInfo", 1)
    perp = [s["symbol"] for s in info["symbols"]
            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"]
    onboard = {s["symbol"]: s.get("onboardDate", 0) for s in info["symbols"]}

    push_log("смотрю обороты за 24 часа…")
    tick = await get_json(session, "/fapi/v1/ticker/24hr", 40)
    qv = {t["symbol"]: float(t["quoteVolume"]) for t in tick}

    in_band = [s for s in perp if qv.get(s, 0) >= CFG["minQv24"] and qv.get(s, 0) <= CFG["maxQv24"]]
    peak = math.log10(1e7)
    in_band.sort(key=lambda s: abs(math.log10(max(qv[s], 1)) - peak))
    cap = max(50, int(CFG.get("maxPairs", 600)))
    dropped = max(0, len(in_band) - cap)
    watchlist = in_band[:cap]
    if "BTCUSDT" not in watchlist:
        watchlist.append("BTCUSDT")

    for sym in watchlist:
        S[sym] = new_sym_state(sym, onboard.get(sym, 0))
        S[sym]["qv24"] = qv.get(sym, 0.0)

    push_log(f"вотчлист: {len(watchlist)} пар, оборот {fmt_usd(CFG['minQv24'])}–{fmt_usd(CFG['maxQv24'])}$")
    if dropped:
        push_log(f"в полосу попало {len(in_band)} пар, потолок отрезал {dropped} "
                  f"— поднимите «пар в вотчлисте»", "warn")
    return watchlist


async def fetch_history_one(session: aiohttp.ClientSession, sym: str):
    try:
        k15 = await get_json(session, "/fapi/v1/klines", 2,
                              dict(symbol=sym, interval="15m", limit=KLINE15_LIMIT))
        k1d = await get_json(session, "/fapi/v1/klines", 1,
                              dict(symbol=sym, interval="1d", limit=DAILY_LIMIT))
        bars = [kline_rest_to_bar(k) for k in k15]
        # последняя свеча из REST может быть незакрытой — убираем
        if bars and now_ms() < bars[-1]["t"] + 900000:
            bars.pop()
        st = S[sym]
        st["bars"] = deque(bars, maxlen=BARS_KEEP)
        st["daily"] = [kline_rest_to_bar(k) | dict(qv=float(k[7])) for k in k1d]
    except Exception as e:
        S.pop(sym, None)
        log.debug("история %s не загружена: %s", sym, e)


async def fetch_all_history(session: aiohttp.ClientSession, syms: list[str]):
    DIAG["history_total"] = len(syms)
    sem = asyncio.Semaphore(HIST_CONCURRENCY)

    async def one(sym):
        async with sem:
            await fetch_history_one(session, sym)
            DIAG["history_done"] += 1

    push_log(f"гружу историю по {len(syms)} парам…")
    await asyncio.gather(*(one(s) for s in syms))
    if "BTCUSDT" in S:
        global BTC15
        BTC15 = S["BTCUSDT"]["bars"]
    live = list(S.keys())
    push_log(f"история загружена по {len(live)} парам")


# --------------------------------------------------------------------------- оценка сигнала
def shelf_status(st: dict, f: dict | None) -> str:
    if f is None:
        return "—"
    if f["sw"] <= CFG["maxWidth"] and f["satr"] <= CFG["maxSatr"] and f["age"] >= 12:
        return "armed"
    if f["sw"] <= CFG["maxWidth"]:
        return "quiet"
    return "—"


def evaluate(sym: str, intrabar: bool):
    st = S.get(sym)
    if not st or len(st["bars"]) < 200:
        return
    bars = list(st["bars"])
    if intrabar and st["live"] is not None:
        bars = bars + [st["live"]]
    st["_btc15"] = list(BTC15) if BTC15 else None
    f = M.compute_features(st, bars)
    if f is None:
        return
    st["feats"] = f
    DIAG["evals"] += 1

    gate = M.passes_gate(f, st["qv24"], CFG)
    st["score"] = M.score_of(f, st["qv24"]) if gate else 0.0

    if math.isfinite(f["rvol"]) and f["rvol"] > DIAG["max_rvol"]:
        DIAG["max_rvol"] = f["rvol"]
        DIAG["max_rvol_sym"] = sym

    shape_ok = (f["sw"] <= CFG["maxWidth"] and f["satr"] <= CFG["maxSatr"]
                and f["rexp"] >= CFG["minRexp"] and f["brk"] >= CFG["minBrk"]
                and f["brk"] <= CFG["maxChase"] and abs(f["sdrift"]) <= CFG["maxDrift"]
                and f["ret"] > 0 and CFG["minQv24"] <= st["qv24"] <= CFG["maxQv24"])
    st["ready"] = min(1.0, f["rvol"] / max(CFG["minRvol"], 1e-6)) if (shape_ok and math.isfinite(f["rvol"])) else 0.0
    if st["ready"] >= 0.5:
        DIAG["near_miss"] += 1

    # статичная (в рамках текущего бара) часть шлюза — полка не меняется, пока
    # не закроется 15м бар. Кэшируем, чтобы дешёвая live-оценка ниже могла
    # обновлять RVOL/«готов» на каждый тик, а не только когда полный
    # пересчёт признаков вообще случился (см. update_live_ready)
    if st.get("cache"):
        st["cache"]["shelf_ok"] = (f["sw"] <= CFG["maxWidth"] and f["satr"] <= CFG["maxSatr"]
                                    and abs(f["sdrift"]) <= CFG["maxDrift"])

    if gate:
        st["state"] = "fired" if st["score"] >= CFG["minScore"] else "gate"
    else:
        st["state"] = shelf_status(st, f)

    if not gate or st["score"] < CFG["minScore"]:
        return

    t = now_ms()
    if t - st["last_sig"] < CFG["cooldownMin"] * 60000:
        return
    muted = [x for x in str(CFG.get("muteHours", "")).split(",") if x.strip().isdigit()]
    muted = [int(x) for x in muted]
    if f["hour"] in muted:
        return

    st["last_sig"] = t
    emit_signal(st, f, intrabar)


def update_live_ready(st: dict, bar: dict):
    """Дешёвая live-оценка RVOL/«готов» на каждый тик форминг-бара — O(1),
    без пересчёта скользящих окон. Полный evaluate() (дорогой) вызывается
    только когда пара уже почти у порога объёма; без этой функции RVOL
    в таблице для остальных пар обновлялся бы только раз в 15 минут,
    на закрытии свечи, хотя выглядел бы как «живой»."""
    cache = st.get("cache")
    if not cache:
        return
    med_qv_l = cache.get("med_qv_l") or 0
    hi = cache.get("hi") or 0
    atr_l = cache.get("atr_l") or 0
    if med_qv_l <= 0 or hi <= 0 or atr_l <= 0:
        return
    bars = st["bars"]
    prev_c = bars[-1]["c"] if bars else 0
    rvol = bar["qv"] / med_qv_l
    rexp = (bar["h"] - bar["l"]) / atr_l
    brk = bar["c"] / hi - 1
    shape_ok = (cache.get("shelf_ok", False) and rexp >= CFG["minRexp"]
                and CFG["minBrk"] <= brk <= CFG["maxChase"]
                and prev_c > 0 and bar["c"] > prev_c
                and CFG["minQv24"] <= st["qv24"] <= CFG["maxQv24"])
    st["live_rvol"] = rvol
    st["live_brk"] = brk
    st["live_ready"] = min(1.0, rvol / max(CFG["minRvol"], 1e-6)) if shape_ok else 0.0


def burst_diag(st: dict) -> dict | None:
    """Диагностика по РЕАЛЬНЫМ минуткам: как набирался объём внутри текущего
    форминг-бара. Не влияет на score — проверено, что как признак модели это
    только вредит на имеющейся выборке (переобучение)."""
    m1 = st.get("m1")
    if not m1:
        return None
    bar_t = st["bars"][-1]["t"] if st["live"] is None else st["live"]["t"]
    mins = [b for b in m1 if bar_t <= b["t"] < bar_t + 900000]
    if len(mins) < 3:
        return None
    mins.sort(key=lambda b: b["t"])
    total = sum(b["qv"] for b in mins)
    if total <= 0:
        return None
    frac1 = mins[0]["qv"] / total
    frac3 = sum(b["qv"] for b in mins[:3]) / total
    shape = "рывком" if frac1 >= 0.4 else ("накоплением" if frac3 <= 0.5 else "смешанно")
    return dict(minutes=len(mins), frac1=frac1, frac3=frac3, shape=shape)


def emit_signal(st: dict, f: dict, intrabar: bool):
    p = M.prob_for(st["score"])
    sig = dict(
        id=f"{st['sym']}-{f['bar_time']}-{'i' if intrabar else 'c'}",
        sym=st["sym"], ts=now_ms(), score=st["score"], price=f["price"], qv24=st["qv24"],
        sw=f["sw"], rvol=f["rvol"], brk=f["brk"], age=f["age"], satr=f["satr"], taker=f["taker"],
        shelf_hi=f["shelf_hi"], shelf_lo=f["shelf_lo"], natr=f["natr"], intrabar=intrabar,
        why=M.explain(f, st["score"]), p=p, burst=burst_diag(st),
    )
    SIGNALS.insert(0, sig)
    del SIGNALS[200:]
    save_signals()
    push_log(f"СИГНАЛ {st['sym']} score {round(st['score']*100)} rvol ×{f['rvol']:.0f}", "hot")
    asyncio.ensure_future(send_telegram(sig))


def save_signals():
    try:
        SIGNALS_PATH.write_text(json.dumps(SIGNALS[:200], ensure_ascii=False), "utf-8")
    except Exception:
        pass


def load_signals():
    global SIGNALS
    if SIGNALS_PATH.exists():
        try:
            SIGNALS = json.loads(SIGNALS_PATH.read_text("utf-8"))
        except Exception:
            SIGNALS = []


# --------------------------------------------------------------------------- Telegram
async def send_telegram(sig: dict):
    if not CFG.get("tgOn") or not CFG.get("tgToken") or not CFG.get("tgChat"):
        # молча пропускать нельзя — сигнал уже виден в списке, но без этой
        # строки в журнале непонятно, почему Telegram молчит
        push_log("сигнал не отправлен в Telegram: заполните и включите Telegram в «Настройках»", "warn")
        return
    text = telegram_text(sig)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"https://api.telegram.org/bot{CFG['tgToken']}/sendMessage",
                json=dict(chat_id=CFG["tgChat"], text=text, parse_mode="HTML",
                          disable_web_page_preview=True),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                j = await r.json()
                if not j.get("ok"):
                    push_log(f"Telegram: {j.get('description','ошибка')}", "dn")
    except Exception as e:
        push_log(f"Telegram недоступен: {e}", "dn")


def stop_level(sig: dict) -> float:
    below = sig["shelf_lo"] * 0.997
    return max(below, sig["price"] * 0.91)


def telegram_text(s: dict) -> str:
    lift10 = round(s["p"]["p10"] / M.BASE["p10"], 1)
    stop = stop_level(s)
    lines = [
        f"🟡 <b>{s['sym']}</b> · тихая полка → вынос", "",
        f"score <b>{round(s['score']*100)}</b>/100{' · внутри бара' if s['intrabar'] else ''}",
        f"цена <b>{fmt_px(s['price'])}</b> · 24ч оборот {fmt_usd(s['qv24'])}$",
        f"полка {s['sw']*100:.1f}% / 12ч · возраст {s['age']} баров",
        f"объём бара ×{s['rvol']:.0f} · пробой {s['brk']*100:+.1f}%", "",
        f"<i>{' · '.join(s['why'])}</i>", "",
    ]
    if s.get("burst"):
        b = s["burst"]
        lines.append(f"набор объёма: {b['shape']} (первая минута {b['frac1']*100:.0f}%, "
                      f"первые три {b['frac3']*100:.0f}% от объёма бара)")
        lines.append("")
    lines += [
        "историч. частота для такого score (отложенные 69 дней):",
        f"+5% за 2ч — <b>{s['p']['p5']*100:.0f}%</b> · +10% — <b>{s['p']['p10']*100:.0f}%</b> (×{lift10} к базе)",
        f"+20% — <b>{s['p']['p20']*100:.1f}%</b> · +40% — {s['p']['p40']*100:.1f}%", "",
        f"потолок полки {fmt_px(s['shelf_hi'])} · низ {fmt_px(s['shelf_lo'])}",
        f"техн. инвалидация ниже {fmt_px(stop)}", "",
        f'<a href="https://www.binance.com/ru/futures/{s["sym"]}">график</a>',
    ]
    return "\n".join(lines)


def fmt_usd(v: float) -> str:
    if v is None or not math.isfinite(v):
        return "—"
    if v >= 1e9:
        return f"{v/1e9:.2f}B"
    if v >= 1e6:
        return f"{v/1e6:.1f}M"
    if v >= 1e3:
        return f"{v/1e3:.0f}K"
    return f"{v:.0f}"


def fmt_px(v: float) -> str:
    if v is None or not math.isfinite(v):
        return "—"
    if v >= 1000:
        return f"{v:.1f}"
    if v >= 1:
        return f"{v:.3f}"
    if v >= 0.01:
        return f"{v:.5f}"
    return f"{v:.6g}"


# --------------------------------------------------------------------------- WS-стримы Binance
async def ws_ticker_loop(session: aiohttp.ClientSession):
    url = f"{WS_BASE}/ws/!ticker@arr"
    while True:
        try:
            async with session.ws_connect(url, heartbeat=20, timeout=20) as ws:
                push_log("тикер-поток подключён")
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        arr = json.loads(msg.data)
                    except Exception:
                        continue
                    if not isinstance(arr, list):
                        continue
                    for t in arr:
                        st = S.get(t.get("s"))
                        if st is None:
                            continue
                        st["qv24"] = float(t.get("q", st["qv24"]))
                        st["price"] = float(t.get("c", st["price"]))
        except Exception as e:
            push_log(f"тикер-поток оборвался: {e}", "warn")
        await asyncio.sleep(3)


def handle_kline15(sym: str, k: dict):
    st = S.get(sym)
    if st is None:
        return
    bar = kline_ws_to_bar(k)
    st["price"] = bar["c"]
    st["last_tick_at"] = now_ms()
    if k.get("x"):
        bars = st["bars"]
        if not bars or bar["t"] > bars[-1]["t"]:
            bars.append(bar)
        elif bar["t"] == bars[-1]["t"]:
            bars[-1] = bar
        st["live"] = None
        st["live_rvol"] = st["live_brk"] = st["live_ready"] = None
        DIAG["bars"] += 1
        DIAG["last_bar_ts"] = now_ms()
        if sym == "BTCUSDT":
            global BTC15
            BTC15 = st["bars"]
        evaluate(sym, False)
    else:
        st["live"] = bar
        update_live_ready(st, bar)
        if not CFG.get("intrabar"):
            return
        bars = st["bars"]
        if not bars or not (bar["c"] > bars[-1]["c"]):
            return
        cache = st.get("cache")
        if cache:
            med_qv_l = cache.get("med_qv_l") or 0
            hi = cache.get("hi") or 0
            if med_qv_l <= 0 or bar["qv"] < CFG["minRvol"] * med_qv_l:
                return
            if hi <= 0 or not (hi * (1 + CFG["minBrk"]) <= bar["c"] <= hi * (1 + CFG["maxChase"])):
                return
        t = now_ms()
        if t - st["last_eval"] < 4000:
            return
        st["last_eval"] = t
        evaluate(sym, True)


def handle_kline1m(sym: str, k: dict):
    """Настоящая закрытая минутная свеча — только для диагностики набора объёма."""
    if not k.get("x"):
        return
    st = S.get(sym)
    if st is None:
        return
    st["m1"].append(kline_ws_to_bar(k))


async def ws_kline_loop(session: aiohttp.ClientSession, syms: list[str], interval: str, handler):
    if not syms:
        return
    streams = "/".join(f"{s.lower()}@kline_{interval}" for s in syms)
    url = f"{WS_BASE}/stream?streams={streams}"
    key = "15" if interval == "15m" else "1m"
    while True:
        try:
            async with session.ws_connect(url, heartbeat=20, timeout=20) as ws:
                if key == "15":
                    DIAG["ws_ok15"] += 1
                else:
                    DIAG["ws_ok1m"] += 1
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        env = json.loads(msg.data)
                    except Exception:
                        continue
                    d = env.get("data") or env
                    k = d.get("k") if d else None
                    if not k:
                        continue
                    DIAG["ws_msgs"] += 1
                    DIAG["last_msg_ts"] = now_ms()
                    handler(d.get("s"), k)
        except Exception as e:
            DIAG["ws_fail_streak"] += 1
            DIAG["ws_last_error"] = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            DIAG["ws_last_error_ts"] = now_ms()
            # первый отказ, потом раз в ~20 секунд — чтобы не спамить журнал,
            # но и не спрятать проблему в log.debug, откуда её никто не увидит
            if DIAG["ws_fail_streak"] in (1, 2) or DIAG["ws_fail_streak"] % 5 == 0:
                push_log(f"поток kline_{interval} ({len(syms)} пар) не подключается: "
                         f"{DIAG['ws_last_error']}", "warn" if DIAG["ws_fail_streak"] < 5 else "dn")
        else:
            if DIAG["ws_fail_streak"] > 0:
                push_log(f"поток kline_{interval} ({len(syms)} пар) восстановлен после "
                         f"{DIAG['ws_fail_streak']} неудачных попыток")
            DIAG["ws_fail_streak"] = 0
            DIAG["ws_ever_connected"] = True
        finally:
            if key == "15":
                DIAG["ws_ok15"] = max(0, DIAG["ws_ok15"] - 1)
            else:
                DIAG["ws_ok1m"] = max(0, DIAG["ws_ok1m"] - 1)
        await asyncio.sleep(4)


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


async def start_streams(session: aiohttp.ClientSession, syms: list[str]):
    tasks = [asyncio.create_task(ws_ticker_loop(session))]
    chunks15 = list(chunk(syms, CHUNK_SYMS))
    chunks1m = list(chunk(syms, CHUNK_SYMS))
    DIAG["ws_total15"] = len(chunks15)
    DIAG["ws_total1m"] = len(chunks1m)
    for c in chunks15:
        tasks.append(asyncio.create_task(ws_kline_loop(session, c, "15m", handle_kline15)))
    for c in chunks1m:
        tasks.append(asyncio.create_task(ws_kline_loop(session, c, "1m", handle_kline1m)))
    push_log(f"потоки подняты: {len(chunks15)}×kline_15m, {len(chunks1m)}×kline_1m, 1×тикер")
    return tasks


# --------------------------------------------------------------------------- ожидаемая частота (для диагностики)
def expected_rate_per_day() -> float:
    th = CFG["minScore"]
    calib = M.CALIB
    if th <= calib[0]["t"]:
        rate = calib[0]["day"]
    elif th >= calib[-1]["t"]:
        rate = calib[-1]["day"]
    else:
        rate = calib[-1]["day"]
        for a, b in zip(calib, calib[1:]):
            if a["t"] <= th <= b["t"]:
                u = (th - a["t"]) / (b["t"] - a["t"])
                rate = a["day"] + (b["day"] - a["day"]) * u
                break
    n = len(S)
    return rate * min(1.0, n / 507) if n else 0.0


# --------------------------------------------------------------------------- снапшот состояния для дашборда
def build_state_snapshot() -> dict:
    rows = []
    rank = dict(fired=4, gate=3, armed=2, quiet=1)
    for st in S.values():
        if st["sym"] == "BTCUSDT" or st["feats"] is None:
            continue
        f = st["feats"]
        # live_* обновляется на каждый тик форминг-бара (см. update_live_ready);
        # f["rvol"] и st["ready"] — только на полном пересчёте (закрытие бара
        # или уже близкая к порогу пара). Предпочитаем live, если он есть —
        # иначе таблица выглядит "живой", а на деле висит до 15 минут.
        live_rvol = st.get("live_rvol")
        live_brk = st.get("live_brk")
        live_ready = st.get("live_ready")
        rows.append(dict(
            sym=st["sym"], score=st["score"],
            ready=(live_ready if live_ready is not None else st["ready"]),
            price=st["price"] or f["price"], sw=f["sw"],
            rvol=(live_rvol if live_rvol is not None else f["rvol"]),
            brk=(live_brk if live_brk is not None else f["brk"]),
            qv24=st["qv24"], state=st["state"], age=f["age"],
            last_tick_at=st.get("last_tick_at") or 0,
        ))
    # тайбрейкер по времени последней реальной сделки — не трогает смысловую
    # сортировку (score/state/ready), но заставляет нижнюю часть списка, где
    # у всех score=0, реально тасоваться по мере поступления тиков (как в JS)
    rows.sort(key=lambda r: (-r["score"], -rank.get(r["state"], 0), -(r["ready"] or 0),
                              -r["last_tick_at"], -r["qv24"]))
    rows = rows[:80]

    armed = sum(1 for st in S.values() if st["state"] == "armed")
    n = len(S)
    best = max((st for st in S.values() if st["sym"] != "BTCUSDT"),
               key=lambda st: st["ready"] or 0, default=None)
    age_s = round((now_ms() - DIAG["last_bar_ts"]) / 1000) if DIAG["last_bar_ts"] else None
    rate = expected_rate_per_day()
    gap_h = 24 / max(rate, 0.01)

    def p_quiet(h):
        return math.exp(-rate * h / 24) * 100

    # соединение считается мёртвым в двух случаях: (1) после загрузки истории
    # нет вообще ни одного живого потока дольше цикла переподключения, или
    # (2) поток формально "подключён" (ws_ok15>0), но давно нет НИ ОДНОГО
    # реального сообщения — та же ловушка, что и в JS: aiohttp heartbeat
    # обычно сам ловит такое через ping/pong, но полагаться только на это
    # рискованно, если сеть глушит и пинги тоже
    ws_dead = DIAG["ready"] and (
        (DIAG["ws_ok15"] == 0 and (now_ms() - DIAG["started"]) > 15000)
        or (DIAG["ws_ok15"] > 0 and DIAG["last_msg_ts"] and (now_ms() - DIAG["last_msg_ts"]) > 30000)
    )
    msg_age_s = round((now_ms() - DIAG["last_msg_ts"]) / 1000) if DIAG["last_msg_ts"] else None
    stats = dict(
        pairs=n, armed=armed, signals=len(SIGNALS),
        ws15=f"{DIAG['ws_ok15']}/{DIAG['ws_total15']}", ws1m=f"{DIAG['ws_ok1m']}/{DIAG['ws_total1m']}",
        last_bar_age_s=age_s,
        best_sym=(best["sym"] if best and best["ready"] else None),
        best_ready=(round((best["ready"] or 0) * 100) if best else 0),
        history_done=DIAG["history_done"], history_total=DIAG["history_total"],
        ready=DIAG["ready"], ws_dead=ws_dead,
        ws_last_error=DIAG["ws_last_error"], ws_fail_streak=DIAG["ws_fail_streak"],
        ws_msgs=DIAG["ws_msgs"], msg_age_s=msg_age_s,
    )
    diag = dict(
        build=BUILD,
        uptime_min=round((now_ms() - DIAG["started"]) / 60000),
        bars=DIAG["bars"], evals=DIAG["evals"],
        max_rvol=round(DIAG["max_rvol"], 1), max_rvol_sym=DIAG["max_rvol_sym"],
        near_miss=DIAG["near_miss"],
        expected_per_day=round(rate, 1), expected_gap_h=round(gap_h, 1),
        p_quiet_2h=round(p_quiet(2), 0), p_quiet_4h=round(p_quiet(4), 0),
        p_quiet_8h=round(p_quiet(8), 0), p_quiet_12h=round(p_quiet(12), 1),
    )
    return dict(stats=stats, diag=diag, radar=rows, signals=SIGNALS[:60], cfg=CFG,
                log=list(LOG_LINES)[:40])



def json_safe(o):
    """Рекурсивно заменяет NaN/Infinity на null: браузерный JSON.parse не понимает
    эти нестандартные токены, которые json.dumps по умолчанию пропускает."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, deque)):
        return [json_safe(v) for v in o]
    return o


# --------------------------------------------------------------------------- веб-сервер
routes = web.RouteTableDef()


@routes.get("/")
async def index(request):
    return web.FileResponse(BASE_DIR / "dashboard.html")


@routes.get("/api/state")
async def api_state(request):
    return web.json_response(json_safe(build_state_snapshot()))


NUMERIC_FIELDS = {k for k, v in M.DEFAULTS.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


@routes.post("/api/config")
async def api_config(request):
    body = await request.json()
    changed_restart = False
    rejected = []
    for k in M.DEFAULTS:
        if k not in body:
            continue
        v = body[k]
        if k in NUMERIC_FIELDS:
            if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                rejected.append(k)   # пустое/битое поле — оставляем прежнее значение, не роняем сканер
                continue
        old = CFG.get(k)
        CFG[k] = v
        if k in ("maxPairs", "minQv24", "maxQv24") and old != v:
            changed_restart = True
    save_config(CFG)
    if rejected:
        push_log(f"не сохранены пустые/некорректные поля: {', '.join(rejected)}", "warn")
    push_log("настройки сохранены")
    return web.json_response(dict(ok=True, needs_restart=changed_restart, rejected=rejected))


@routes.post("/api/test_telegram")
async def api_test_telegram(request):
    body = await request.json()
    token = body.get("tgToken") or CFG.get("tgToken")
    chat = body.get("tgChat") or CFG.get("tgChat")
    if not token or not chat:
        return web.json_response(dict(ok=False, error="нужны токен и chat id"))
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=dict(chat_id=chat, parse_mode="HTML",
                          text="✅ <b>ShelfScan (Python)</b> на связи.\nСюда будут прилетать выносы с тихой полки."),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                j = await r.json()
        if j.get("ok"):
            push_log("тестовое сообщение отправлено")
        return web.json_response(dict(ok=j.get("ok", False), error=j.get("description")))
    except Exception as e:
        return web.json_response(dict(ok=False, error=str(e)))


@routes.post("/api/clear_signals")
async def api_clear_signals(request):
    SIGNALS.clear()
    save_signals()
    return web.json_response(dict(ok=True))


async def periodic_pulse():
    while True:
        await asyncio.sleep(15 * 60)
        best = max((st for st in S.values() if st["sym"] != "BTCUSDT"),
                   key=lambda st: st["ready"] or 0, default=None)
        bstr = f", ближе всех {best['sym']} {round((best['ready'] or 0)*100)}%" if best and best["ready"] else ", близких нет"
        push_log(f"пульс: свечей {DIAG['bars']}, макс RVOL за сессию ×{DIAG['max_rvol']:.1f}{bstr}")


async def on_startup(app: web.Application):
    session = aiohttp.ClientSession()
    app["session"] = session
    load_signals()
    watchlist = await build_watchlist(session)
    await fetch_all_history(session, watchlist)
    DIAG["ready"] = True
    for sym in list(S.keys()):
        evaluate(sym, False)
    app["stream_tasks"] = await start_streams(session, list(S.keys()))
    app["pulse_task"] = asyncio.create_task(periodic_pulse())
    push_log("сканер запущен, дашборд на http://localhost:%d" % HTTP_PORT)


async def on_cleanup(app: web.Application):
    for t in app.get("stream_tasks", []):
        t.cancel()
    if app.get("pulse_task"):
        app["pulse_task"].cancel()
    await app["session"].close()


def main():
    app = web.Application()
    app.add_routes(routes)
    app.router.add_static("/static", BASE_DIR, show_index=False)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    if not _TRUSTSTORE_OK:
        push_log("truststore не подключён — если Telegram/Binance падают с ошибкой сертификата, "
                 "см. README_PYTHON.md", "warn")
    push_log(f"ShelfScan (Python) build {BUILD} стартует — открой http://localhost:{HTTP_PORT} в браузере")
    web.run_app(app, host="127.0.0.1", port=HTTP_PORT, print=None)


if __name__ == "__main__":
    main()
