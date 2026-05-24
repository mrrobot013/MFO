#!/usr/bin/env bash
# Start a temporary Cloudflare Tunnel to the local parser webhook.

set -euo pipefail

PORT="${1:-8765}"
LOCAL_URL="http://localhost:${PORT}"
PRINTED_MARKER="$(mktemp -t mfo-tunnel-printed.XXXXXX)"

cleanup() {
  rm -f "$PRINTED_MARKER"
}
trap cleanup EXIT

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared не найден."
  echo "Установи его через Homebrew:"
  echo "  brew install cloudflared"
  exit 1
fi

if command -v nc >/dev/null 2>&1; then
  if ! nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
    echo "Внимание: локальный порт ${PORT} пока не отвечает."
    echo "Обычно сначала запускают парсер через ./gui.sh, потом tunnel.sh."
    echo
  fi
fi

echo "Запускаю Cloudflare Tunnel -> ${LOCAL_URL}"
echo "Когда появится trycloudflare-ссылка, вставь URL ниже в Forward SMS."
echo "Остановить туннель: Ctrl+C"
echo

cloudflared tunnel --url "$LOCAL_URL" 2>&1 | while IFS= read -r line; do
  echo "$line"
  if [[ "$line" =~ https://[-a-zA-Z0-9.]+trycloudflare\.com ]]; then
    PUBLIC_URL="${BASH_REMATCH[0]}"
    if [[ ! -s "$PRINTED_MARKER" ]]; then
      echo "printed" > "$PRINTED_MARKER"
      echo
      echo "============================================================"
      echo "Готовые URL для Forward SMS:"
      echo
      echo "SMS:    ${PUBLIC_URL}/sms"
      echo "Звонки: ${PUBLIC_URL}/call"
      echo
      echo "Важно: эта ссылка временная. После перезапуска tunnel.sh"
      echo "нужно заново вставить новые URL в Forward SMS."
      echo "============================================================"
      echo
    fi
  fi
done
