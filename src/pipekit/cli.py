"""CLI entry point for PipeKit.

Usage::

    pipekit list                    List all discoverable pipelines
    pipekit info <name>             Show pipeline details
    pipekit run <name> [--input '{}'] [--file <path>]
    pipekit daemon start|stop|status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .daemon import ensure_daemon, send_request, stop_daemon


def _json_arg(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--input must be a JSON object")
    return parsed


def _parse_flag_kv(tokens: list[str]) -> dict[str, Any]:
    """Parse ``--key value --flag`` style arguments into a dict."""
    out: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            raise ValueError(f"Unexpected positional argument: {token}")

        raw = token[2:]
        if not raw:
            raise ValueError("Invalid empty flag name")

        if "=" in raw:
            key, val = raw.split("=", 1)
            out[key] = _parse_scalar(val)
            i += 1
            continue

        key = raw
        if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
            out[key] = _parse_scalar(tokens[i + 1])
            i += 2
            continue

        out[key] = True
        i += 1
    return out


def _parse_scalar(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        pass
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower == "null":
        return None
    return value


async def _run(args: argparse.Namespace) -> None:
    await ensure_daemon()

    # Remap shortcuts (run / list / info) to pipeline entity
    entity: str = args.entity
    action: str = getattr(args, "action", "") or entity
    if entity in ("run", "list", "info"):
        entity = "pipeline"

    if entity == "daemon":
        if action == "start":
            # ensure_daemon already started it
            resp = await send_request({"entity": "daemon", "action": "ping"})
            _print(resp)
            return
        if action == "stop":
            try:
                resp = await send_request({"entity": "daemon", "action": "stop"})
                _print(resp)
            except Exception:
                stop_daemon()
                _print({"ok": True, "stopped": True})
            return
        if action == "status":
            try:
                resp = await send_request({"entity": "daemon", "action": "status"})
            except Exception:
                resp = {"ok": False, "error": "Daemon not running"}
            _print(resp)
            return

    if entity == "pipeline":
        if action == "list":
            resp = await send_request({"entity": "pipeline", "action": "list"})
            _print(resp.get("data", resp))
            return

        if action == "info":
            resp = await send_request({
                "entity": "pipeline",
                "action": "info",
                "name": args.name,
            })
            _print(resp.get("data", resp))
            return

        if action == "run":
            input_data = _json_arg(args.input) if hasattr(args, "input") else {}
            input_data.update(getattr(args, "extra_args", {}))

            payload: dict[str, Any] = {
                "entity": "pipeline",
                "action": "run",
                "input": input_data,
                "account": getattr(args, "account", None) or "default",
            }

            if getattr(args, "file", None):
                payload["file_path"] = args.file
            else:
                payload["name"] = args.name

            if getattr(args, "cdp", None):
                payload["cdp"] = args.cdp

            resp = await send_request(payload)
            _print(resp.get("data", resp))
            if not resp.get("ok"):
                sys.exit(1)
            return

    raise ValueError(f"Unknown command: {entity}/{action}")


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipekit",
        description="Pipeline toolkit — composable browser-automation pipelines.",
    )
    parser.add_argument("--version", action="version", version="pipekit 0.1.0")
    parser.add_argument("--account", help="Account name for browser profile (default: default)")
    parser.add_argument("--cdp", help="Connect to existing Chrome via CDP port")

    sub = parser.add_subparsers(dest="entity")

    # --- daemon ---
    daemon = sub.add_parser("daemon", help="Manage the PipeKit daemon")
    daemon_sub = daemon.add_subparsers(dest="action", required=True)
    daemon_sub.add_parser("start", help="Start the daemon")
    daemon_sub.add_parser("stop", help="Stop the daemon")
    daemon_sub.add_parser("status", help="Show daemon status")

    # --- shortcuts ---
    run_shortcut = sub.add_parser("run", help="Run a pipeline (shortcut)")
    run_shortcut.add_argument("name", nargs="?", help="Pipeline name or file path")
    run_shortcut.add_argument("--input", help="JSON object with pipeline input")
    run_shortcut.add_argument("--file", help="Explicit .pipeline.py file path")

    _list = sub.add_parser("list", help="List all pipelines (shortcut)")

    info_shortcut = sub.add_parser("info", help="Show pipeline details (shortcut)")
    info_shortcut.add_argument("name", help="Pipeline name")

    # --- pipeline (full subcommands) ---
    pipeline = sub.add_parser("pipeline", help="Manage pipelines")
    pipeline_sub = pipeline.add_subparsers(dest="action", required=True)

    pipeline_sub.add_parser("list", help="List all discoverable pipelines")

    info_parser = pipeline_sub.add_parser("info", help="Show pipeline details")
    info_parser.add_argument("name", help="Pipeline name")

    run_parser = pipeline_sub.add_parser("run", help="Run a pipeline")
    run_parser.add_argument("name", nargs="?", help="Pipeline name or file path")
    run_parser.add_argument("--input", help="JSON object with pipeline input")
    run_parser.add_argument("--file", help="Explicit .pipeline.py file path")

    return parser


def main() -> None:
    parser = build_parser()
    args, unknown = parser.parse_known_args()

    is_run_cmd = getattr(args, "entity", None) in ("pipeline", "run")
    is_run_action = getattr(args, "action", None) in ("run", None)
    if is_run_cmd and is_run_action:
        try:
            parsed = _parse_flag_kv(unknown)
            if "account" in parsed and not getattr(args, "account", None):
                args.account = str(parsed.pop("account"))
            if "cdp" in parsed and not getattr(args, "cdp", None):
                args.cdp = str(parsed.pop("cdp"))
            if "file" in parsed and not getattr(args, "file", None):
                args.file = str(parsed.pop("file"))
            args.extra_args = parsed
        except ValueError as exc:
            parser.error(str(exc))
    elif unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")

    try:
        asyncio.run(_run(args))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)
