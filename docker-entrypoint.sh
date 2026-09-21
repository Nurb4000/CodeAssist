#!/bin/bash
# Docker entrypoint that reads port from config.toml.
#
# Config resolution order (highest wins):
#   1. baked-in default  -> /app/config.docker.toml  (container runs out of the box)
#   2. host override file -> /app/override.config.toml (mounted from ./config.toml)
# The effective config is materialised at /app/config.toml, which the app reads.
#
# Note: a bind-mount source that does not exist on the host becomes an empty
# *directory* inside the container. The `-f` guards below treat that as "no
# override" and fall back to the baked default instead of failing at boot.

CONFIG_FILE="/app/config.toml"
FALLBACK="/app/config.docker.toml"
OVERRIDE="/app/override.config.toml"
DEFAULT_PORT=8090

# Start from the baked-in Docker default so a valid config always exists.
if [ -f "$FALLBACK" ]; then
    cp "$FALLBACK" "$CONFIG_FILE"
fi

# Prefer a host-provided override when it is a real file.
if [ -f "$OVERRIDE" ]; then
    cp "$OVERRIDE" "$CONFIG_FILE"
fi

# Fail fast with a clear message when no usable config exists at all.
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: config file not found at $CONFIG_FILE" >&2
    echo "Copy config.docker.toml (or config.example.toml) to ./config.toml in the" >&2
    echo "compose project root and restart." >&2
    exit 1
fi

# Extract the listen port from [server] port (overridable via SERVER_PORT env).
PORT=$(grep -A 5 '^\[server\]' "$CONFIG_FILE" | grep '^port' | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')
PORT=${SERVER_PORT:-${PORT:-$DEFAULT_PORT}}

echo "Starting CodeAssist on port $PORT"

exec python -m uvicorn codeassist.server:app --host 0.0.0.0 --port "$PORT"
