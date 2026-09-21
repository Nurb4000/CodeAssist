#!/bin/bash
# Docker entrypoint that reads port from config.toml.
#
# Config is baked into the image (config.docker.toml -> /app/config.toml) so the
# container boots with zero host files. Runtime settings (LLM identity, etc.)
# are managed through the UI and persisted in the DB, not via host TOML — see
# docker-compose.yml. This also sidesteps Docker's bind-mount trap of creating
# an empty ./config.toml directory when the file is absent.

CONFIG_FILE="/app/config.toml"
FALLBACK="/app/config.docker.toml"
DEFAULT_PORT=8090

# Start from the baked-in Docker default so a valid config always exists.
if [ -f "$FALLBACK" ]; then
    cp "$FALLBACK" "$CONFIG_FILE"
fi

# Fail fast with a clear message when no usable config exists at all.
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: config file not found at $CONFIG_FILE" >&2
    echo "The image should ship config.docker.toml; this should never happen." >&2
    exit 1
fi

# Extract the listen port from [server] port (overridable via SERVER_PORT env).
PORT=$(grep -A 5 '^\[server\]' "$CONFIG_FILE" | grep '^port' | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')
PORT=${SERVER_PORT:-${PORT:-$DEFAULT_PORT}}

echo "Starting CodeAssist on port $PORT"

exec python -m uvicorn codeassist.server:app --host 0.0.0.0 --port "$PORT"
