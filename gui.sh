#!/usr/bin/env bash
# Запуск пользовательского интерфейса парсера МФО.
# Использование: ./gui.sh   (или: bash gui.sh)

set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Сначала создай виртуальное окружение и поставь зависимости:"
  echo "  python3.11 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  exit 1
fi

if [ ! -f ".venv/bin/activate" ]; then
  echo "В папке .venv найдено Windows-окружение или битый venv: нет .venv/bin/activate"
  echo "На Mac пересоздай окружение:"
  echo "  rm -rf .venv"
  echo "  python3.11 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate

if ! python -c "import streamlit" >/dev/null 2>&1; then
  echo "Streamlit не установлен. Ставлю…"
  pip install -r requirements.txt
fi

echo "Открываю интерфейс на http://localhost:8501 …"
echo "Важно: интерфейс откроется в Safari, а Chrome будет использоваться только парсером."

streamlit run app.py \
  --server.headless=true \
  --server.port=8501 \
  --browser.gatherUsageStats=false &

pid=$!
sleep 3

open -a Safari http://localhost:8501 2>/dev/null || open http://localhost:8501

wait "$pid"
