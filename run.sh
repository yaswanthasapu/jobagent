#!/usr/bin/env bash
# Runner for JobAgent on macOS and Linux

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    exec "$SCRIPT_DIR/.venv/bin/python" -m jobagent "$@"
elif [ -f "$HOME/.jobagent/venv/bin/python" ]; then
    exec "$HOME/.jobagent/venv/bin/python" -m jobagent "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 -m jobagent "$@"
else
    echo "Python not found. Please run ./install.sh first."
    exit 1
fi
