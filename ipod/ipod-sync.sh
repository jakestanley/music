#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJ_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -d "$PROJ_DIR/.venv" ]; then
    echo "Creating venv..."
    python3 -m venv "$PROJ_DIR/.venv"
fi

source "$PROJ_DIR/.venv/bin/activate"
pip install -r "$PROJ_DIR/requirements.txt" -q
exec python3 "$SCRIPT_DIR/ipod-sync.py" "$@"
