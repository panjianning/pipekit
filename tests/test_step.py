"""Tests for StepManager."""

from __future__ import annotations

from pathlib import Path

from pipekit.pipeline.step import StepManager
from pipekit.pipeline.types import RunState


def _make_run() -> RunState:
    return RunState(
        run_id="test-run",
        pipeline_name="test/x",
        work_dir=Path("/tmp/test"),
    )


class TestStepManager:
    def test_start_step(self) -> None:
        mgr = StepManager()
        run = _make_run()
        step = mgr.start(run, "browser", "navigate to example.com")

        assert step.type == "browser"
        assert step.status == "running"
        assert step.summary == "navigate to example.com"
        assert len(run.steps) == 1
        assert run.steps[0] is step

    def test_step_ids_increment(self) -> None:
        mgr = StepManager()
        run = _make_run()
        s1 = mgr.start(run, "browser", "first")
        s2 = mgr.start(run, "pipeline", "second")
        s3 = mgr.start(run, "artifact", "third")
        assert s1.id == "browser_001"
        assert s2.id == "pipeline_002"
        assert s3.id == "artifact_003"

    def test_finish_completed(self) -> None:
        mgr = StepManager()
        run = _make_run()
        step = mgr.start(run, "browser", "click button")
        mgr.finish(run, step, "completed", summary="clicked .btn")

        assert step.status == "completed"
        assert step.summary == "clicked .btn"
        assert step.ended_at is not None

    def test_finish_failed(self) -> None:
        mgr = StepManager()
        run = _make_run()
        step = mgr.start(run, "browser", "bad action")
        mgr.finish(run, step, "failed", error="timeout")

        assert step.status == "failed"
        assert step.error == "timeout"

    def test_finish_with_artifact(self) -> None:
        mgr = StepManager()
        run = _make_run()
        step = mgr.start(run, "artifact", "write result")
        mgr.finish(run, step, "completed", artifact_path="/tmp/out.json")

        assert step.artifact_path == "/tmp/out.json"

    def test_log_appended(self) -> None:
        mgr = StepManager()
        run = _make_run()
        mgr.start(run, "browser", "test log")
        assert len(run.logs) > 0
        assert "START" in run.logs[0]

    def test_finish_log(self) -> None:
        mgr = StepManager()
        run = _make_run()
        step = mgr.start(run, "browser", "test")
        initial_len = len(run.logs)
        mgr.finish(run, step, "completed")
        assert len(run.logs) > initial_len
