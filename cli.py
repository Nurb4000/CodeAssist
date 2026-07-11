#!/usr/bin/env python3
"""CodeAssist - AI coding assistant for local development."""

import argparse
import sys
import webbrowser
import threading
import time


def main():
    parser = argparse.ArgumentParser(
        prog="codeassist",
        description="AI coding assistant - runs locally on your machine",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--workspace", default=".", help="Working directory (default: current dir)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    parser.add_argument("--config", default="config.toml", help="Config file path (default: config.toml)")
    args = parser.parse_args()

    import uvicorn

    url = f"http://{args.host}:{args.port}"

    if not args.no_browser:
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n  CodeAssist running at {url}")
    print(f"  Workspace: {args.workspace}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        uvicorn.run(
            "server:app",
            host=args.host,
            port=args.port,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
