#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  ShelfScan (Python) — запуск"
echo "============================================"
echo

if ! command -v python3 &>/dev/null; then
  echo "Python 3 не найден. Установите: https://www.python.org/downloads/"
  exit 1
fi

echo "Проверяю зависимости..."
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt

echo
echo "Запускаю сканер. Дашборд: http://localhost:8765"
echo "Для остановки — Ctrl+C."
echo

( sleep 1 && (command -v open &>/dev/null && open http://localhost:8765 || command -v xdg-open &>/dev/null && xdg-open http://localhost:8765 || true) ) &
python3 app.py
