"""Daemon — long-lived process that manages browser sessions.

The daemon keeps Chrome alive between ``pipekit run`` commands, avoiding
the overhead of restarting the browser for every pipeline invocation.

Architecture::

    pipekit run → CLI → ensure_daemon() → send_request()
                                           ↓
    DaemonServer ←── TCP 127.0.0.1:{port} ← {entity, action, ...}
      ├── BrowserSession per account
      ├── PipelineRunner
      └── idle timeout → auto-shutdown
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any

from .browser import BrowserSession, ContextStore, _pipekit_root
from .browser.actions import (
    click,
    evaluate,
    fill,
    navigate,
    snapshot,
    take_screenshot,
    type_text,
    wait_for,
)
from .pipeline.runner import PipelineRunner

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _runtime_dir() -> Path:
    return _pipekit_root() / "run"


def _port_path() -> Path:
    return _runtime_dir() / "daemon.port"


def _pid_path() -> Path:
    return _runtime_dir() / "daemon.pid"


def _ensure_runtime_dir() -> None:
    _runtime_dir().mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# DaemonServer
# ---------------------------------------------------------------------------


class DaemonServer:
    """Lightweight TCP server that manages browser sessions and pipeline runs."""

    IDLE_TIMEOUT_S = 600  # 10 minutes

    def __init__(self) -> None:
        self._runner = PipelineRunner()
        self._sessions: dict[str, BrowserSession] = {}
        self._contexts = ContextStore()
        self._server: asyncio.AbstractServer | None = None
        self._idle_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def serve(self) -> None:
        """Start listening on a random port, write port/pid to disk."""
        _ensure_runtime_dir()
        self._idle_task = asyncio.create_task(self._idle_cleaner())

        self._server = await asyncio.start_server(
            self._handle_client, host="127.0.0.1", port=0
        )
        sockets = self._server.sockets or []
        port = int(sockets[0].getsockname()[1])

        _pid_path().write_text(str(os.getpid()), encoding="utf-8")
        _port_path().write_text(str(port), encoding="utf-8")

        logger.info("Daemon listening on 127.0.0.1:%d", port)

        async with self._server:
            await self._server.serve_forever()

    async def shutdown(self) -> None:
        """Gracefully shut down: close all sessions, server, and cleanup pid/port."""
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None

        await self._contexts.destroy_all()

        for session in self._sessions.values():
            try:
                await session.close()
            except Exception:
                logger.debug("Error closing session", exc_info=True)
        self._sessions.clear()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

        for p in [_port_path(), _pid_path()]:
            p.unlink(missing_ok=True)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw:
                return
            req = json.loads(raw.decode("utf-8"))
            logger.debug("REQ %s/%s", req.get("entity"), req.get("action"))
            resp = await self._dispatch(req)
            writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception as exc:
            logger.debug("Client error: %s", exc)
            try:
                err = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                writer.write((err + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def _idle_cleaner(self) -> None:
        """Periodically close idle sessions and contexts."""
        while True:
            await asyncio.sleep(30)

            # Clean idle contexts
            await self._contexts.cleanup_idle()

            # Clean idle sessions
            to_remove = [
                name
                for name, session in self._sessions.items()
                if session.idle_seconds > self.IDLE_TIMEOUT_S
            ]
            for name in to_remove:
                with contextlib.suppress(Exception):
                    await self._sessions[name].close()
                del self._sessions[name]

            # Shut down if nothing is alive
            if not self._sessions and not self._contexts.list() and self._server is not None:
                logger.info("All sessions and contexts idle — shutting down daemon")
                asyncio.create_task(self.shutdown())

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        entity = str(req.get("entity", ""))
        action = str(req.get("action", ""))

        try:
            return await self._dispatch_impl(req)
        except Exception as exc:
            logger.exception("Dispatch error %s/%s", entity, action)
            return {"ok": False, "error": str(exc)}

    async def _dispatch_impl(self, req: dict[str, Any]) -> dict[str, Any]:
        entity = str(req.get("entity", ""))
        action = str(req.get("action", ""))

        # -- daemon commands --
        if entity == "daemon" and action == "ping":
            return {"ok": True, "pid": os.getpid(), "sessions": list(self._sessions.keys())}

        if entity == "daemon" and action == "stop":
            asyncio.create_task(self.shutdown())
            return {"ok": True}

        if entity == "daemon" and action == "status":
            return {
                "ok": True,
                "pid": os.getpid(),
                "sessions": [
                    {"key": k, "idle_seconds": round(s.idle_seconds, 1)}
                    for k, s in self._sessions.items()
                ],
            }

        # -- pipeline commands --
        cdp = req.get("cdp")
        account = str(req.get("account", "default"))

        if entity == "pipeline" and action == "list":
            pipelines = self._runner.list_pipelines()
            return {
                "ok": True,
                "data": [
                    {
                        "name": p.name,
                        "description": p.description,
                        "tags": p.tags,
                        "source": p.source,
                        "file_path": str(p.file_path) if p.file_path else None,
                    }
                    for p in pipelines
                ],
            }

        if entity == "pipeline" and action == "info":
            name = str(req.get("name", "")).strip()
            meta = self._runner.get_pipeline(name)
            if meta is None:
                return {"ok": False, "error": f"Pipeline not found: {name}"}
            return {
                "ok": True,
                "data": {
                    "name": meta.name,
                    "description": meta.description,
                    "tags": meta.tags,
                    "source": meta.source,
                    "input": {
                        k: {
                            "required": v.required,
                            "description": v.description,
                            "default": v.default,
                        }
                        for k, v in meta.input.items()
                    },
                    "output": {
                        k: {"type": v.type, "description": v.description}
                        for k, v in meta.output.items()
                    },
                },
            }

        if entity == "pipeline" and action == "run":
            name = str(req.get("name", ""))
            file_path = req.get("file_path")
            input_data = req.get("input") if isinstance(req.get("input"), dict) else {}

            session = await self._get_session(account, cdp)

            if file_path:
                run = await self._runner.run_by_path(str(file_path), input_data, session)
            else:
                run = await self._runner.run_by_name(name, input_data, session)

            return {
                "ok": run.status == "completed",
                "data": {
                    "run_id": run.run_id,
                    "pipeline_name": run.pipeline_name,
                    "status": run.status,
                    "result": run.result,
                    "error": run.error,
                    "steps": len(run.steps),
                    "errors": [s.error for s in run.steps if s.error],
                },
            }

        # -- context commands --
        if entity == "context":
            account = str(req.get("account", "default"))

            if action == "create":
                name = str(req.get("name", "")).strip()
                if not name:
                    return {"ok": False, "error": "context create requires name"}
                session = await self._get_session(account, cdp)
                result = await self._contexts.create(name, session)
                return {"ok": True, "data": result}

            if action == "list":
                return {"ok": True, "data": self._contexts.list()}

            if action == "destroy":
                name = str(req.get("name", "")).strip()
                result = await self._contexts.destroy(name)
                return {"ok": True, "data": result}

        # -- browser atomic actions --
        if entity == "browser":
            ctx_name = str(req.get("context", "")).strip()
            entry = self._contexts.get(ctx_name)
            params = req.get("params") if isinstance(req.get("params"), dict) else {}

            if action == "navigate":
                url = str(params.get("url", ""))
                if not url:
                    return {"ok": False, "error": "navigate requires url"}
                data = await navigate(entry, url)
                return {"ok": True, "data": data}

            if action == "snapshot":
                sel = params.get("selector")
                compact = params.get("compact", True)
                data = await snapshot(entry, selector=sel, compact=compact)
                return {"ok": True, "data": data}

            if action == "click":
                sel = str(params.get("selector", ""))
                if not sel:
                    return {"ok": False, "error": "click requires selector"}
                data = await click(entry, sel)
                return {"ok": True, "data": data}

            if action == "fill":
                sel = str(params.get("selector", ""))
                val = str(params.get("value", ""))
                if not sel:
                    return {"ok": False, "error": "fill requires selector"}
                data = await fill(entry, sel, val)
                return {"ok": True, "data": data}

            if action == "type":
                sel = str(params.get("selector", ""))
                text = str(params.get("text", ""))
                if not sel:
                    return {"ok": False, "error": "type requires selector"}
                data = await type_text(entry, sel, text)
                return {"ok": True, "data": data}

            if action == "evaluate":
                script = str(params.get("script", ""))
                if not script:
                    return {"ok": False, "error": "evaluate requires script"}
                data = await evaluate(entry, script)
                return {"ok": True, "data": data}

            if action == "screenshot":
                path = params.get("path")
                sel = params.get("selector")
                full = bool(params.get("full_page", False))
                data = await take_screenshot(entry, path=path, selector=sel, full_page=full)
                return {"ok": True, "data": data}

            if action == "wait":
                sel = params.get("selector")
                timeout = int(params.get("timeout", 3000))
                state = str(params.get("state", "visible"))
                data = await wait_for(entry, selector=sel, timeout=timeout, state=state)
                return {"ok": True, "data": data}

        return {"ok": False, "error": f"Unknown command: {entity}/{action}"}

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _get_session(self, account_name: str, cdp: str | None) -> BrowserSession:
        key = f"cdp:{cdp}" if cdp else account_name
        if key in self._sessions:
            return self._sessions[key]

        logger.info("Creating browser session account=%s cdp=%s", account_name, cdp)
        session = BrowserSession(account_name=account_name, cdp_target=cdp)
        await session.ensure_browser()
        self._sessions[key] = session
        return session


# ---------------------------------------------------------------------------
# Client helpers (used by CLI)
# ---------------------------------------------------------------------------


async def send_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Send a JSON-line request to the running daemon."""
    port = int(_port_path().read_text(encoding="utf-8").strip())
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=60)
        if not raw:
            return {"ok": False, "error": "Empty daemon response"}
        return json.loads(raw.decode("utf-8"))
    finally:
        writer.close()
        await writer.wait_closed()


def _daemon_alive() -> bool:
    return _port_path().exists()


async def ensure_daemon() -> None:
    """Ensure the daemon is running, start it if not."""
    if _daemon_alive():
        return
    _ensure_runtime_dir()

    # Find the Python executable
    import sys as _sys

    argv = [_sys.executable, "-m", "pipekit.daemon", "--serve"]
    await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    for _ in range(50):  # 5 seconds
        if _daemon_alive():
            return
        await asyncio.sleep(0.1)

    raise RuntimeError("Daemon failed to start within 5 seconds")


def stop_daemon() -> None:
    """Forcefully terminate the daemon process by PID."""
    pidf = _pid_path()
    if not pidf.exists():
        return
    try:
        pid = int(pidf.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Standalone entry-point (--serve)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipekit-daemon")
    parser.add_argument("--serve", action="store_true", help="Run as daemon server")
    return parser


def daemon_main() -> None:
    """Entry point invoked by ``python -m pipekit.daemon --serve``."""
    parser = _build_parser()
    args = parser.parse_args()
    if not args.serve:
        parser.error("Use --serve to start the daemon")
    asyncio.run(_serve_main())


async def _serve_main() -> None:
    server = DaemonServer()
    try:
        await server.serve()
    finally:
        await server.shutdown()


if __name__ == "__main__":
    daemon_main()
