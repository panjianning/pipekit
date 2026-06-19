"""Pipeline runner — top-level orchestration for a ``pipekit run`` command."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..browser import BrowserSession
from .discover import PipelineDiscover
from .executor import PipelineExecutor
from .step import StepManager
from .types import PipelineMeta, RunState


class PipelineRunner:
    """Create runs, resolve pipelines, and execute them in isolated contexts.

    Each call to :meth:`run` creates:
    1. A unique ``RunState`` with work directory
    2. An isolated BrowserContext (cloned from master's login state)
    3. Executes the pipeline, then destroys the context
    """

    def __init__(self, working_dir: str | Path | None = None) -> None:
        self._working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self._discover = PipelineDiscover(self._working_dir)
        self._steps = StepManager()
        self._executor = PipelineExecutor(self._steps)

        # Runtime directory for run artifacts
        self._runs_dir = self._working_dir / ".pipekit" / "runs"
        self._runs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_by_name(
        self,
        name: str,
        input_data: dict[str, Any],
        session: BrowserSession,
    ) -> RunState:
        """Run a pipeline discovered by name."""
        resolved = self._discover.resolve(name)
        if resolved is None:
            raise ValueError(f"Pipeline not found: {name}")
        meta, run_fn = resolved
        return await self._run_pipeline(meta, run_fn, input_data, session)

    async def run_by_path(
        self,
        file_path: str | Path,
        input_data: dict[str, Any],
        session: BrowserSession,
    ) -> RunState:
        """Run a pipeline from an explicit file path."""
        meta, run_fn = self._discover.resolve_file(file_path)
        return await self._run_pipeline(meta, run_fn, input_data, session)

    def list_pipelines(self) -> list[PipelineMeta]:
        """Return all discoverable pipelines."""
        return self._discover.discover_all()

    def get_pipeline(self, name: str) -> PipelineMeta | None:
        """Get pipeline metadata by name."""
        resolved = self._discover.resolve(name)
        return resolved[0] if resolved else None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self,
        meta: PipelineMeta,
        run_fn: Any,
        input_data: dict[str, Any],
        session: BrowserSession,
    ) -> RunState:
        # Merge input with defaults
        merged = self._merge_input(meta, input_data)

        # Create run state
        run_id = self._make_run_id()
        work_dir = self._runs_dir / run_id
        work_dir.mkdir(parents=True, exist_ok=True)

        run = RunState(
            run_id=run_id,
            pipeline_name=meta.name,
            work_dir=work_dir,
            input=merged,
        )

        # Isolate browser context
        browser_ctx = await session.isolate_with_login()
        try:
            # Build sub-pipeline resolver (closure over discover + session)
            async def _sub_resolver(name: str) -> Any:
                resolved = self._discover.resolve(name)
                if resolved is None:
                    return None
                return resolved

            async def _sub_executor(loaded: Any, sub_input: dict[str, Any]) -> dict[str, Any]:
                sub_meta, sub_run_fn = loaded
                sub_run = RunState(
                    run_id=f"{run_id}_sub_{int(time.time() * 1000)}",
                    pipeline_name=sub_meta.name,
                    work_dir=work_dir,
                    input=sub_input,
                )
                await self._executor.execute(
                    sub_meta, sub_run_fn, sub_run, browser_ctx,
                    sub_resolver=_sub_resolver,
                    sub_executor=_sub_executor,
                )
                return sub_run.result or {}

            await self._executor.execute(
                meta, run_fn, run, browser_ctx,
                sub_resolver=_sub_resolver,
                sub_executor=_sub_executor,
            )
            return run
        finally:
            await BrowserSession.close_context_safe(browser_ctx)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _merge_input(self, meta: PipelineMeta, input_data: dict[str, Any]) -> dict[str, Any]:
        merged = dict(input_data)
        for key, defn in meta.input.items():
            if key not in merged and defn.default is not None:
                merged[key] = defn.default
            missing = key not in merged or merged[key] is None or (
                isinstance(merged[key], str) and not merged[key].strip()
            )
            if defn.required and missing:
                raise ValueError(f"Pipeline '{meta.name}' missing required input: {key}")
        return merged

    @staticmethod
    def _make_run_id() -> str:
        return f"run_{int(time.time() * 1000)}_{__import__('secrets').token_hex(4)}"
