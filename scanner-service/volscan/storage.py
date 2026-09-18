"""SQLite persistence.

Design notes
------------
* WAL mode + ``synchronous=NORMAL``: readers (the dashboard) never block the
  writer, and a crash costs at most the current batch, not the database.
* Nothing is written per tick. Rows accumulate in memory and are flushed on a
  timer (``Config.flush_sec``, default 10s) and on shutdown — the browser build
  serialised the whole market on every tick, which is what made it freeze.
* Everything the engine needs to resume lives here: tracked-pair baselines, the
  signal journal, forward-outcome tracking and a rolling price/volume history
  per pair for the dashboard's drill-down chart.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Dict, Iterable, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE TABLE IF NOT EXISTS pairs (
    key           TEXT PRIMARY KEY,
    market        TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    base          TEXT,
    session_base  REAL,
    base_time     INTEGER,
    price_base    REAL,
    day_high      REAL,
    day_high_at   INTEGER,
    day_low       REAL,
    cluster_at    INTEGER,
    signal_count  INTEGER DEFAULT 0,
    updated_at    INTEGER
);

CREATE TABLE IF NOT EXISTS signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    key        TEXT NOT NULL,
    market     TEXT,
    symbol     TEXT,
    kind       TEXT NOT NULL,
    score      INTEGER,
    price      REAL,
    reasons    TEXT,
    features   TEXT,
    alerted    INTEGER DEFAULT 0,
    p5 REAL, p15 REAL, p30 REAL, p60 REAL, p240 REAL,
    mfe REAL DEFAULT 0, mfe_min REAL DEFAULT 0,
    mae REAL DEFAULT 0, mae_min REAL DEFAULT 0,
    done       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_signals_ts   ON signals(ts DESC);
CREATE INDEX IF NOT EXISTS ix_signals_key  ON signals(key, ts DESC);
CREATE INDEX IF NOT EXISTS ix_signals_open ON signals(done, ts);

CREATE TABLE IF NOT EXISTS samples (
    key   TEXT NOT NULL,
    ts    INTEGER NOT NULL,
    price REAL,
    qv    REAL,
    d1m   REAL,
    rvol  REAL,
    score INTEGER,
    accel INTEGER DEFAULT 0,
    PRIMARY KEY (key, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_samples_ts ON samples(ts);

CREATE TABLE IF NOT EXISTS settings (
    k          TEXT PRIMARY KEY,
    v          TEXT,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS live (
    key     TEXT PRIMARY KEY,
    ts      INTEGER,
    payload TEXT
);
"""


class Storage:
    def __init__(self, path: str):
        self.path = path
        first = not os.path.exists(path)
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._signals: List[tuple] = []
        self._samples: List[tuple] = []
        self._live: Dict[str, tuple] = {}
        self._pairs: Dict[str, tuple] = {}
        if first:
            self.set_meta("created_at", str(int(time.time() * 1000)))

    # ------------------------------------------------------------------ meta
    def set_meta(self, k: str, v: str) -> None:
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def get_meta(self, k: str, default=None):
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    # ------------------------------------------------------------- settings
    # Written straight through (no batching): a settings change is rare, and the
    # person clicking "OK" needs it to survive an immediate restart.
    def load_settings(self) -> Dict[str, str]:
        return {r["k"]: r["v"] for r in
                self.db.execute("SELECT k,v FROM settings").fetchall()}

    def save_settings(self, values: Dict[str, object]) -> None:
        now = int(time.time() * 1000)
        self.db.executemany(
            "INSERT INTO settings(k,v,updated_at) VALUES(?,?,?)"
            " ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
            [(k, json.dumps(v), now) for k, v in values.items()])
        self.db.commit()

    def settings_updated_at(self) -> int:
        row = self.db.execute("SELECT MAX(updated_at) m FROM settings").fetchone()
        return row["m"] or 0

    # --------------------------------------------------------------- buffers
    def queue_signal(self, sig, alerted: bool) -> None:
        self._signals.append((
            sig.ts, sig.key, sig.market, sig.symbol, sig.kind, sig.score, sig.price,
            " · ".join(sig.reasons[:6]), json.dumps(sig.features, separators=(",", ":")),
            1 if alerted else 0,
        ))

    def queue_sample(self, key: str, ts: int, price, qv, d1m, rvol, score, accel) -> None:
        self._samples.append((key, ts, price, qv, d1m, rvol, score, 1 if accel else 0))

    def queue_pair(self, st) -> None:
        self._pairs[st.key] = (
            st.key, st.market, st.symbol, st.base, st.session_base, st.base_time,
            st.price_base, st.day_high, st.day_high_at, st.day_low, st.cluster_at,
            st.signal_count, st.last_tick,
        )

    def queue_live(self, key: str, ts: int, payload: dict) -> None:
        self._live[key] = (key, ts, json.dumps(payload, separators=(",", ":"), default=_j))

    # ----------------------------------------------------------------- flush
    def flush(self) -> int:
        n = len(self._signals) + len(self._samples) + len(self._pairs) + len(self._live)
        if not n:
            return 0
        cur = self.db.cursor()
        if self._signals:
            cur.executemany(
                "INSERT INTO signals(ts,key,market,symbol,kind,score,price,reasons,features,alerted)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)", self._signals)
            self._signals.clear()
        if self._samples:
            cur.executemany(
                "INSERT OR REPLACE INTO samples(key,ts,price,qv,d1m,rvol,score,accel)"
                " VALUES(?,?,?,?,?,?,?,?)", self._samples)
            self._samples.clear()
        if self._pairs:
            cur.executemany(
                "INSERT INTO pairs(key,market,symbol,base,session_base,base_time,price_base,"
                "day_high,day_high_at,day_low,cluster_at,signal_count,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET session_base=excluded.session_base,"
                " base_time=excluded.base_time, price_base=excluded.price_base,"
                " day_high=excluded.day_high, day_high_at=excluded.day_high_at,"
                " day_low=excluded.day_low, cluster_at=excluded.cluster_at,"
                " signal_count=excluded.signal_count, updated_at=excluded.updated_at",
                list(self._pairs.values()))
            self._pairs.clear()
        if self._live:
            cur.executemany(
                "INSERT INTO live(key,ts,payload) VALUES(?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET ts=excluded.ts, payload=excluded.payload",
                list(self._live.values()))
            self._live.clear()
        self.db.commit()
        return n

    # -------------------------------------------------------------- outcomes
    def open_signals(self, max_age_ms: int = 5 * 3600 * 1000) -> List[sqlite3.Row]:
        cutoff = int(time.time() * 1000) - max_age_ms
        return self.db.execute(
            "SELECT id,key,ts,price,mfe,mae,mfe_min,mae_min,p5,p15,p30,p60,p240"
            " FROM signals WHERE done=0 AND ts>=?", (cutoff,)).fetchall()

    def update_outcomes(self, rows: Iterable[tuple]) -> None:
        self.db.executemany(
            "UPDATE signals SET p5=?,p15=?,p30=?,p60=?,p240=?,mfe=?,mfe_min=?,"
            "mae=?,mae_min=?,done=? WHERE id=?", list(rows))
        self.db.commit()

    def close_stale(self, max_age_ms: int = 5 * 3600 * 1000) -> None:
        cutoff = int(time.time() * 1000) - max_age_ms
        self.db.execute("UPDATE signals SET done=1 WHERE done=0 AND ts<?", (cutoff,))
        self.db.commit()

    # ------------------------------------------------------------- retention
    def prune(self, samples_keep_ms: int = 24 * 3600 * 1000,
              signals_keep_ms: int = 30 * 24 * 3600 * 1000) -> None:
        now = int(time.time() * 1000)
        self.db.execute("DELETE FROM samples WHERE ts<?", (now - samples_keep_ms,))
        self.db.execute("DELETE FROM signals WHERE ts<?", (now - signals_keep_ms,))
        self.db.commit()

    # ------------------------------------------------------------- restoring
    def load_pairs(self) -> List[sqlite3.Row]:
        return self.db.execute("SELECT * FROM pairs").fetchall()

    # ------------------------------------------------------------ dashboard
    def live_rows(self, limit: int = 300) -> List[dict]:
        rows = self.db.execute(
            "SELECT key,ts,payload FROM live ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def recent_signals(self, limit: int = 200, key: Optional[str] = None) -> List[dict]:
        if key:
            rows = self.db.execute(
                "SELECT * FROM signals WHERE key=? ORDER BY ts DESC LIMIT ?", (key, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def history(self, key: str, since_ms: int) -> List[dict]:
        rows = self.db.execute(
            "SELECT ts,price,qv,d1m,rvol,score,accel FROM samples"
            " WHERE key=? AND ts>=? ORDER BY ts", (key, since_ms)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        q = self.db.execute
        out = {
            "pairs": q("SELECT COUNT(*) c FROM pairs").fetchone()["c"],
            "signals": q("SELECT COUNT(*) c FROM signals").fetchone()["c"],
            "alerts": q("SELECT COUNT(*) c FROM signals WHERE alerted=1").fetchone()["c"],
            "samples": q("SELECT COUNT(*) c FROM samples").fetchone()["c"],
        }
        row = q("SELECT COUNT(*) n, AVG(CASE WHEN mfe>=10 THEN 1.0 ELSE 0.0 END) hit"
                " FROM signals WHERE done=1 AND kind IN ('ACCEL','CANDIDATE')").fetchone()
        out["closed_alerts"] = row["n"] or 0
        out["hit10"] = round((row["hit"] or 0) * 100, 1)
        return out

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self.db.close()


def _j(o):
    try:
        return float(o)
    except Exception:
        return str(o)
