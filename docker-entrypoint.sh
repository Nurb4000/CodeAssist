#!/bin/bash
# Docker entrypoint that reads port from config.toml

CONFIG_FILE="/app/config.toml"
DEFAULT_PORT=8090

# Fail fast with a clear message when config.toml is missing. It's gitignored
# and must be copied from config.docker.toml / config.example.toml; without it
# the server starts, then later fails with a confusing mount/port error.
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: config file not found at $CONFIG_FILE" >&2
    echo "Copy config.docker.toml (or config.example.toml) to config.toml, set your values, and restart." >&2
    exit 1
fi

# Try to extract port from config.toml
if [ -f "$CONFIG_FILE" ]; then
    # Look for port in [server] section
    PORT=$(grep -A 5 '^\[server\]' "$CONFIG_FILE" | grep '^port' | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')
fi

# Use extracted port or default
PORT=${PORT:-$DEFAULT_PORT}

echo "Starting CodeAssist on port $PORT"

exec python -m uvicorn codeassist.server:app --host 0.0.0.0 --port "$PORT"
