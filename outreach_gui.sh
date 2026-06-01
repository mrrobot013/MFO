#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -r ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

python -m streamlit run outreach_app.py "$@"
