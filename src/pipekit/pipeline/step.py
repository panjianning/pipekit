"""Step lifecycle tracking for pipeline runs."""

from __future__ import annotations

import time

from .types import RunState, StepState


class StepManager:
    """Track individual steps within a pipeline run."""

    def start(self, run: RunState, step_type: str, summary: str) -> StepState:
        """Create and register a new running step."""
        n = len(run.steps) + 1
        step_id = f"{step_type}_{n:03d}"
        step = StepState(
            id=step_id,
            type=step_type,
            status="running",
            started_at=time.time(),
            summary=summary,
        )
        run.steps.append(step)
        self._append_log(run, f"[{step_id}] START {step_type} — {summary}")
        return step

    def finish(
        self,
        run: RunState,
        step: StepState,
        status: str,
        *,
        summary: str | None = None,
        error: str | None = None,
        exit_code: int | None = None,
        artifact_path: str | None = None,
    ) -> None:
        """Mark a step as completed / failed / canceled."""
        step.status = status
        step.ended_at = time.time()
        if summary is not None:
            step.summary = summary
        if error is not None:
            step.error = error
        if exit_code is not None:
            step.exit_code = exit_code
        if artifact_path is not None:
            step.artifact_path = artifact_path

        extra = f" — {step.summary}" if step.summary else ""
        self._append_log(run, f"[{step.id}] {status.upper()}{extra}")

    @staticmethod
    def _append_log(run: RunState, line: str) -> None:
        run.logs.append(f"[{time.strftime('%H:%M:%S')}] {line}")
