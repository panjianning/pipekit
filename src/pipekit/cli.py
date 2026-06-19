"""CLI entry point for PipeKit.

Usage::

    pipekit list                    List all pipelines
    pipekit info <name>             Show pipeline details
    pipekit run <name> [--input '{}']
    pipekit context create|list|destroy
    pipekit navigate|snapshot|click|fill|evaluate|screenshot|wait
    pipekit daemon start|stop|status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .daemon import ensure_daemon, send_request, stop_daemon

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> None:
    await ensure_daemon()

    entity: str = args.entity
    action: str = getattr(args, "action", "") or entity
    if entity in ("run", "list", "info"):
        entity = "pipeline"

    if entity == "daemon":
        await _dispatch_daemon(args, action)
        return

    if entity == "context":
        await _dispatch_context(args, action)
        return

    if entity == "pipeline":
        await _dispatch_pipeline(args, action)
        return

    # Browser atomic actions
    if entity in (
        "navigate", "snapshot", "click", "fill", "type",
        "evaluate", "screenshot", "wait",
    ):
        await _dispatch_browser_atom(args, entity)
        return

    raise ValueError(f"Unknown command: {entity}/{action}")


async def _dispatch_daemon(args: argparse.Namespace, action: str) -> None:
    if action == "start":
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


async def _dispatch_context(args: argparse.Namespace, action: str) -> None:
    if action == "create":
        account = (getattr(args, "account", None) or "default")
        resp = await send_request({
            "entity": "context",
            "action": "create",
            "name": getattr(args, "name", ""),
            "account": account,
        })
        _print(resp)
        return
    if action == "list":
        resp = await send_request({"entity": "context", "action": "list"})
        _print(resp.get("data", resp))
        return
    if action == "destroy":
        resp = await send_request({
            "entity": "context",
            "action": "destroy",
            "name": getattr(args, "name", ""),
        })
        _print(resp)


async def _dispatch_pipeline(args: argparse.Namespace, action: str) -> None:
    if action == "list":
        resp = await send_request({"entity": "pipeline", "action": "list"})
        _print(resp.get("data", resp))
        return
    if action == "info":
        resp = await send_request({
            "entity": "pipeline", "action": "info",
            "name": args.name,
        })
        _print(resp.get("data", resp))
        return
    if action == "run":
        input_data = _json_arg(args.input) if hasattr(args, "input") else {}
        input_data.update(getattr(args, "extra_args", {}))
        payload: dict[str, Any] = {
            "entity": "pipeline", "action": "run",
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


async def _dispatch_browser_atom(args: argparse.Namespace, action: str) -> None:
    ctx = getattr(args, "context", "")
    if not ctx:
        print(json.dumps({"ok": False, "error": "--context is required"}))
        sys.exit(1)
    params: dict[str, Any] = {}
    for key in ("url", "selector", "value", "text", "script", "path",
                  "timeout", "state", "full_page", "compact"):
        val = getattr(args, key, None)
        if val is not None:
            params[key] = val

    resp = await send_request({
        "entity": "browser",
        "action": action,
        "context": ctx,
        "params": params,
    })
    if not resp.get("ok"):
        print(json.dumps(resp, ensure_ascii=False, indent=2))
        sys.exit(1)
    _print(resp.get("data", resp))


def _print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipekit",
        description="Pipeline toolkit — composable browser-automation pipelines.",
    )
    parser.add_argument("--version", action="version", version="pipekit 0.1.0")
    parser.add_argument("--account", help="Account name for browser profile")
    parser.add_argument("--cdp", help="Connect to existing Chrome via CDP port")

    sub = parser.add_subparsers(dest="entity")

    # --- daemon ---
    daemon = sub.add_parser("daemon", help="Manage the PipeKit daemon")
    daemon_sub = daemon.add_subparsers(dest="action", required=True)
    daemon_sub.add_parser("start")
    daemon_sub.add_parser("stop")
    daemon_sub.add_parser("status")

    # --- shortcuts ---
    _add_shortcuts(sub)

    # --- context ---
    ctx_cmd = sub.add_parser(
        "context", help="Manage named browser contexts"
    )
    ctx_sub = ctx_cmd.add_subparsers(dest="action", required=True)
    ctx_create = ctx_sub.add_parser("create", help="Create a new context")
    ctx_create.add_argument("name", help="Context name")
    ctx_sub.add_parser("list", help="List active contexts")
    ctx_destroy = ctx_sub.add_parser("destroy", help="Close a context")
    ctx_destroy.add_argument("name", help="Context name")

    # --- browser atoms ---
    _browser_parent = argparse.ArgumentParser(add_help=False)
    _browser_parent.add_argument("--context", required=True, help="Context name")

    nav = sub.add_parser("navigate", parents=[_browser_parent], help="Navigate to URL")
    nav.add_argument("--url", required=True)

    snap = sub.add_parser("snapshot", parents=[_browser_parent], help="Capture page snapshot")
    snap.add_argument("--selector")
    snap.add_argument("--compact", type=bool, default=True)

    clk = sub.add_parser("click", parents=[_browser_parent], help="Click element")
    clk.add_argument("--selector", required=True)

    fl = sub.add_parser("fill", parents=[_browser_parent], help="Fill input")
    fl.add_argument("--selector", required=True)
    fl.add_argument("--value", required=True)

    tp = sub.add_parser("type", parents=[_browser_parent], help="Type text")
    tp.add_argument("--selector", required=True)
    tp.add_argument("--text", required=True)

    ev = sub.add_parser("evaluate", parents=[_browser_parent], help="Execute JavaScript")
    ev.add_argument("--script", required=True)

    ss = sub.add_parser("screenshot", parents=[_browser_parent], help="Take screenshot")
    ss.add_argument("--selector")
    ss.add_argument("--path")
    ss.add_argument("--full_page", type=bool, default=False)

    wt = sub.add_parser("wait", parents=[_browser_parent], help="Wait for condition")
    wt.add_argument("--selector")
    wt.add_argument("--timeout", type=int, default=3000)

    # --- pipeline ---
    pipeline = sub.add_parser("pipeline", help="Manage pipelines")
    pipeline_sub = pipeline.add_subparsers(dest="action", required=True)
    pipeline_sub.add_parser("list")
    info_parser = pipeline_sub.add_parser("info")
    info_parser.add_argument("name")
    run_parser = pipeline_sub.add_parser("run")
    run_parser.add_argument("name", nargs="?")
    run_parser.add_argument("--input")
    run_parser.add_argument("--file")

    return parser


def _add_shortcuts(sub: Any) -> None:
    """Top-level shortcuts: run, list, info."""
    run_sc = sub.add_parser("run", help="Run a pipeline")
    run_sc.add_argument("name", nargs="?")
    run_sc.add_argument("--input")
    run_sc.add_argument("--file")

    sub.add_parser("list", help="List all pipelines")

    info_sc = sub.add_parser("info", help="Show pipeline details")
    info_sc.add_argument("name")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    args, unknown = parser.parse_known_args()

    # Allow --account and --cdp anywhere in the command line
    if unknown:
        try:
            parsed = _parse_flag_kv(unknown)
            if "account" in parsed and not getattr(args, "account", None):
                args.account = str(parsed.pop("account"))
            if "cdp" in parsed and not getattr(args, "cdp", None):
                args.cdp = str(parsed.pop("cdp"))
            if "file" in parsed and not getattr(args, "file", None):
                args.file = str(parsed.pop("file"))
            if "context" in parsed and not getattr(args, "context", None):
                args.context = str(parsed.pop("context"))
            # Remaining unknown args become extra pipeline input
            if parsed:
                if not hasattr(args, "extra_args"):
                    args.extra_args = {}
                args.extra_args.update(parsed)
        except ValueError as exc:
            parser.error(str(exc))

    try:
        asyncio.run(_run(args))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)
