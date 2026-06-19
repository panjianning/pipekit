"""Pipeline executor — runs a pipeline module with step tracking."""

from __future__ import annotations

import json
import time
from typing import Any

from .context import PipelineContext
from .step import StepManager
from .types import PipelineMeta, RunState


class PipelineExecutor:
    """Execute a loaded pipeline within an isolated browser context."""

    def __init__(self, steps: StepManager | None = None) -> None:
        self._steps = steps or StepManager()

    async def execute(
        self,
        meta: PipelineMeta,
        run_fn: Any,  # async def run(ctx) -> dict
        run: RunState,
        browser_context: Any,  # playwright.async_api.BrowserContext
        *,
        sub_resolver: Any = None,
        sub_executor: Any = None,
    ) -> None:
        """Run a pipeline. Updates *run* in-place with steps, logs, and result."""
        run.status = "running"
        run.started_at = time.time()

        ctx = PipelineContext(
            input_data=run.input,
            work_dir=run.work_dir,
            browser_context=browser_context,
            steps=self._steps,
            run=run,
            pipeline_resolver=sub_resolver,
            pipeline_executor=sub_executor,
        )

        try:
            self._log(run, f"Pipeline start: {meta.name}")
            result = await run_fn(ctx)

            if not isinstance(result, dict):
                raise TypeError(
                    f"Pipeline '{meta.name}' must return a dict, got {type(result).__name__}"
                )

            run.result = result
            run.status = "completed"
            run.ended_at = time.time()

            # Persist result
            (run.work_dir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            self._log(run, f"Pipeline completed: {meta.name}")

        except Exception as exc:
            run.ended_at = time.time()
            run.status = "failed" if not run.cancel_requested else "canceled"
            error_msg = f"{type(exc).__name__}: {exc}"
            run.error = error_msg
            self._log(run, f"Pipeline {run.status}: {error_msg}")

            # Mark any still-running step as failed
            for step in run.steps:
                if step.status == "running":
                    step.status = run.status
                    step.ended_at = time.time()
                    step.error = error_msg

        finally:
            # Always persist run state snapshot
            self._persist_run(run)

    def _log(self, run: RunState, message: str) -> None:
        run.logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    def _persist_run(self, run: RunState) -> None:
        snapshot = {
            "run_id": run.run_id,
            "pipeline_name": run.pipeline_name,
            "status": run.status,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "input": run.input,
            "result": run.result,
            "error": run.error,
            "steps": [
                {
                    "id": s.id,
                    "type": s.type,
                    "status": s.status,
                    "summary": s.summary,
                    "error": s.error,
                }
                for s in run.steps
            ],
            "logs": run.logs,
        }
        (run.work_dir / "run.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
