#!/usr/bin/env python3
"""CodeAssist - AI coding assistant for local development."""

import argparse
import sys
import threading
import time
import webbrowser


def _add_common_args(parser: argparse.ArgumentParser, *, include_browser: bool = False) -> None:
    parser.add_argument("--host", default=None, help="Host to bind to (default: from config.toml or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on (default: from config.toml or 8000)")
    parser.add_argument("--workspace", default=None, help="Working directory (default: from config.toml or current dir)")
    parser.add_argument("--config", default="config.toml", help="Config file path (default: config.toml)")
    if include_browser:
        parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")


def _load_config(args):
    from codeassist.config import Config

    config = Config.load(args.config)
    if getattr(args, "host", None) is not None:
        config.server.host = args.host
    if getattr(args, "port", None) is not None:
        config.server.port = args.port
    if getattr(args, "workspace", None) is not None:
        config.server.workspace = args.workspace
        config.refresh_workspace()
    return config


def _cmd_serve(args) -> None:
    from codeassist import server

    config = _load_config(args)
    server.set_config(config)

    host = config.server.host
    port = config.server.port
    url = f"http://{host}:{port}"

    if not getattr(args, "no_browser", False):
        def open_browser():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n  CodeAssist running at {url}")
    print(f"  Workspace: {config.workspace}")
    print("  Press Ctrl+C to stop\n")

    try:
        import uvicorn
        uvicorn.run(
            "codeassist.server:app",
            host=host,
            port=port,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)


def _cmd_run(args) -> None:
    import asyncio
    import json

    from codeassist.headless import bootstrap
    from codeassist.headless_run import run_prompt
    from codeassist.session import Session

    config = _load_config(args)

    async def _run() -> int:
        runtime = await bootstrap(config)
        try:
            session_id = args.session
            if args.continue_session:
                sessions = await Session.list_all()
                if sessions:
                    session_id = sessions[0]["id"]
            if not session_id:
                session = await Session.create(name=args.title)
                session_id = session.id
            else:
                await Session.get_or_create(session_id)

            message = " ".join(args.message).strip()
            if not message and not sys.stdin.isatty():
                message = sys.stdin.read().strip()
            if not message:
                print("Error: no message provided. Pass a message or pipe stdin.", file=sys.stderr)
                return 2

            def sink(event):
                if args.format == "json":
                    print(json.dumps({"type": event.type, **event.data}, ensure_ascii=False), flush=True)
                elif args.format == "text" and event.type == "text_delta":
                    print(event.data.get("content", ""), end="", flush=True)

            result = await run_prompt(
                session_id=session_id,
                message=message,
                agent_name=args.agent,
                sink=sink,
            )

            if args.format == "text":
                if not result.text.endswith("\n"):
                    print()
            elif args.format == "final":
                print(result.text)
            elif args.format == "json":
                print(json.dumps({"type": "result", "session_id": result.session_id, "stop_reason": result.stop_reason, "error": result.error}, ensure_ascii=False))
            return 0 if result.stop_reason != "error" else 1
        finally:
            await runtime.close()

    raise SystemExit(asyncio.run(_run()))


def _cmd_acp(args) -> None:
    import asyncio

    from codeassist.acp_adapter import serve_stdio

    config = _load_config(args)
    try:
        asyncio.run(serve_stdio(config))
    except KeyboardInterrupt:
        pass


def main():
    parser = argparse.ArgumentParser(
        prog="codeassist",
        description="AI coding assistant - runs locally on your machine",
    )
    _add_common_args(parser, include_browser=True)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Start web UI server (default)")
    _add_common_args(serve, include_browser=True)
    serve.set_defaults(func=_cmd_serve)

    run = sub.add_parser("run", help="Run a single headless prompt")
    _add_common_args(run)
    run.add_argument("message", nargs="*", help="Prompt text. If omitted, reads stdin.")
    run.add_argument("--session", default=None, help="Existing session ID to continue")
    run.add_argument("--continue", dest="continue_session", action="store_true", help="Continue most recent session")
    run.add_argument("--agent", default=None, help="Agent name/id")
    run.add_argument("--format", choices=["text", "final", "json"], default="text", help="Output format")
    run.add_argument("--title", default=None, help="Title for a newly created session")
    run.set_defaults(func=_cmd_run)

    acp = sub.add_parser("acp", help="Run ACP stdio adapter")
    _add_common_args(acp)
    acp.set_defaults(func=_cmd_acp)

    args = parser.parse_args()
    if not getattr(args, "command", None):
        # backward-compatible default: web server
        _cmd_serve(args)
        return
    args.func(args)


if __name__ == "__main__":
    main()
