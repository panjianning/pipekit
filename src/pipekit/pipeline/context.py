"""Pipeline execution context — the ``ctx`` object passed to ``async def run(ctx)``.

Design aligns with the Tokex pipeline context model:

- ``ctx.input``       — merged & validated input parameters
- ``ctx.work_dir``    — absolute path to this run's working directory
- ``ctx.log(message)``— append a line to the run log
- ``ctx.browser``     — browser actions (navigate, click, evaluate, …)
- ``ctx.pipeline``    — sub-pipeline invocation
- ``ctx.artifact``    — sandboxed file read/write
- ``ctx.utils``       — external commands + path helpers
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..artifact import ArtifactStore
from .step import StepManager
from .types import RunState

# ---------------------------------------------------------------------------
# Browser facade
# ---------------------------------------------------------------------------


class _BrowserFacade:
    """Thin wrapper around Playwright BrowserContext for browser actions."""

    def __init__(self, page_factory: Callable[[], Any]) -> None:
        self._new_page = page_factory

    async def navigate(self, url: str) -> Any:
        """Open a new page and navigate to *url*. Returns the Playwright Page."""
        page = await self._new_page()
        await page.goto(url, wait_until="domcontentloaded")
        return page

    async def evaluate(self, script: str) -> Any:
        """Execute JavaScript in a fresh page. Returns the script's result."""
        page = await self._new_page()
        result = await page.evaluate(script)
        await page.close()
        return result


# ---------------------------------------------------------------------------
# Pipeline facade (sub-pipeline calls)
# ---------------------------------------------------------------------------


class _PipelineFacade:
    """Allow pipelines to call other pipelines by name."""

    def __init__(
        self,
        run: RunState,
        steps: StepManager,
        resolve: Callable[[str], Any],
        execute: Callable[..., Any],
    ) -> None:
        self._run = run
        self._steps = steps
        self._resolve = resolve
        self._execute = execute

    async def run(self, name: str, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke a sub-pipeline by name."""
        pipeline_name = name.strip()
        if not pipeline_name:
            raise ValueError("pipeline.run requires a pipeline name")

        step = self._steps.start(self._run, "pipeline", f"sub-pipeline {pipeline_name}")
        try:
            loaded = self._resolve(pipeline_name)
            if loaded is None:
                raise ValueError(f"Pipeline not found: {pipeline_name}")

            sub_input = {**self._run.input, **(input_data or {})}
            result = await self._execute(loaded, sub_input)
            status = "completed"
            self._steps.finish(
                self._run, step, status, summary=f"sub-pipeline {pipeline_name}"
            )
            return result or {}
        except Exception as exc:
            self._steps.finish(self._run, step, "failed", error=str(exc))
            raise


# ---------------------------------------------------------------------------
# Utils facade
# ---------------------------------------------------------------------------


class _UtilsFacade:
    """Utility methods for pipelines: external commands, path helpers."""

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    async def run_command(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        timeout_ms: int = 30_000,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute an external command. Returns {ok, status, stdout, stderr}."""
        cmd_list = [command] + (args or [])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or str(self._work_dir),
                env={**__import__("os").environ, **(env or {})},
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_ms / 1000
            )
            return {
                "ok": proc.returncode == 0,
                "status": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            }
        except TimeoutError:
            return {"ok": False, "status": None, "stdout": "", "stderr": "Command timed out"}
        except Exception as exc:
            return {"ok": False, "status": None, "stdout": "", "stderr": str(exc)}

    def resolve_path(self, relative: str) -> str:
        """Resolve a relative path against the run's work_dir."""
        return str((self._work_dir / relative).resolve())

    def read_text(self, relative: str) -> str:
        return (self._work_dir / relative).read_text(encoding="utf-8")

    def read_json(self, relative: str) -> Any:
        return json.loads((self._work_dir / relative).read_text(encoding="utf-8"))

    def write_text(self, relative: str, data: str) -> str:
        target = self._work_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")
        return str(target)

    def write_json(self, relative: str, data: Any) -> str:
        target = self._work_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(target)


# ---------------------------------------------------------------------------
# PipelineContext
# ---------------------------------------------------------------------------


class PipelineContext:
    """Execution context for a single pipeline run.

    Holds an isolated BrowserContext so tabs/cookies/storage don't leak
    between runs. Every ``pipekit run`` gets its own context cloned from
    the master (login) context.
    """

    def __init__(
        self,
        input_data: dict[str, Any],
        work_dir: Path,
        browser_context: Any,  # playwright.async_api.BrowserContext
        steps: StepManager,
        run: RunState,
        *,
        pipeline_resolver: Callable[[str], Any] | None = None,
        pipeline_executor: Callable[..., Any] | None = None,
    ) -> None:
        self.input = input_data
        self.work_dir = work_dir
        self._browser_context = browser_context
        self._steps = steps
        self._run = run

        self.artifact = ArtifactStore(work_dir)
        self.browser = _BrowserFacade(lambda: browser_context.new_page())
        self.pipeline = _PipelineFacade(
            run, steps,
            pipeline_resolver or (lambda _: None),
            pipeline_executor or (lambda *_: {}),
        )
        self.utils = _UtilsFacade(work_dir)

    def log(self, message: str) -> None:
        """Append a log message to the run log."""
        self._steps._append_log(self._run, message)

    # Allow pipelines to access the raw page factory for advanced use
    async def new_page(self) -> Any:
        """Open a new Playwright Page in this run's isolated BrowserContext."""
        return await self._browser_context.new_page()
