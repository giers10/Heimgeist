#!/bin/sh
set -eu

PYTHON_BIN="${PYTHON_BIN:-python3.13}"
VENV_DIR="backend/.venv"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.13 is required. Set PYTHON_BIN to a Python 3.13 executable if needed." >&2
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ] || ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)'; then
  rm -rf "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install -r backend/requirements.txt
npm install
npm run dev
