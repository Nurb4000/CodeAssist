#!/bin/bash
# Docker entrypoint that reads port from config.toml

CONFIG_FILE="/app/config.toml"
DEFAULT_PORT=8090

# Try to extract port from config.toml
if [ -f "$CONFIG_FILE" ]; then
    # Look for port in [server] section
    PORT=$(grep -A 5 '^\[server\]' "$CONFIG_FILE" | grep '^port' | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')
fi

# Use extracted port or default
PORT=${PORT:-$DEFAULT_PORT}

echo "Starting CodeAssist on port $PORT"

exec python -m uvicorn server:app --host 0.0.0.0 --port "$PORT"
