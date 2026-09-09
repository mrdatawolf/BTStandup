#!/usr/bin/env bash
# Starts the BT Standup app.
#
# Usage:
#   ./start.sh            start normally against the configured database
#   ./start.sh --demo     seed demo data into data/demo.db and start against
#                          that instead, leaving the real database untouched
#
# Env overrides (optional):
#   DEMO_DATABASE_PATH   path used for --demo (default: ./data/demo.db)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

PYTHON="${APP_DIR}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "Virtual environment not found. Follow the setup steps in README.md." >&2
  exit 1
fi

DEMO=0
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=1 ;;
    -h|--help)
      sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$DEMO" -eq 1 ]]; then
  export DATABASE_PATH="${DEMO_DATABASE_PATH:-./data/demo.db}"
  echo "Seeding demo data into ${DATABASE_PATH}..."
  "$PYTHON" seed_demo.py
fi

exec "$PYTHON" app.py
