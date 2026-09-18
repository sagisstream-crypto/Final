#!/usr/bin/env python3
"""VolScan scanner service entry point.

    python3 run.py --config config.json
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from volscan import __version__
from volscan.config import Config
from volscan.service import Service


def main() -> int:
    ap = argparse.ArgumentParser(description="VolScan Binance scanner service")
    ap.add_argument("--config", default="config.json", help="путь к JSON-конфигу")
    ap.add_argument("--db", default=None, help="путь к файлу базы (перекрывает конфиг)")
    ap.add_argument("--port", type=int, default=None, help="порт дашборда")
    ap.add_argument("--host", default=None, help="адрес дашборда (0.0.0.0 — виден в локальной сети)")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--log-level", default=None)
    args = ap.parse_args()

    cfg = Config.load(args.config)
    if args.db:
        cfg.db_path = args.db
    if args.port:
        cfg.dashboard_port = args.port
    if args.host:
        cfg.dashboard_host = args.host
    if args.no_dashboard:
        cfg.dashboard_enabled = False
    if args.log_level:
        cfg.log_level = args.log_level

    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("volscan")
    log.info("VolScan service v%s — БД %s, порог объёма $%s",
             __version__, cfg.db_path, f"{cfg.max_initial_volume:,.0f}")

    svc = Service(cfg)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    svc.install_signal_handlers(loop)
    try:
        loop.run_until_complete(svc.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
