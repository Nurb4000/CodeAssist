#!/usr/bin/env python3
"""CodeAssist - AI coding assistant for local development."""

import argparse
import sys
import webbrowser
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        prog="codeassist",
        description="AI coding assistant - runs locally on your machine",
    )
    parser.add_argument("--host", default=None, help="Host to bind to (default: from config.toml or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on (default: from config.toml or 8000)")
    parser.add_argument("--workspace", default=None, help="Working directory (default: from config.toml or current dir)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    parser.add_argument("--config", default="config.toml", help="Config file path (default: config.toml)")
    args = parser.parse_args()

    from config import Config
    import server

    config = Config.load(args.config)

    if args.host is not None:
        config.server.host = args.host
    if args.port is not None:
        config.server.port = args.port
    if args.workspace is not None:
        config.server.workspace = args.workspace
        config.workspace = Path(config.server.workspace).resolve()

    server.set_config(config)

    host = config.server.host
    port = config.server.port
    url = f"http://{host}:{port}"

    if not args.no_browser:
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n  CodeAssist running at {url}")
    print(f"  Workspace: {config.workspace}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        import uvicorn
        uvicorn.run(
            "server:app",
            host=host,
            port=port,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
