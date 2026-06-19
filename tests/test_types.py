"""Tests for pipeline types."""

from __future__ import annotations

from pipekit.pipeline.types import InputDef, PipelineMeta, RunState, StepState


class TestInputDef:
    def test_defaults(self) -> None:
        d = InputDef()
        assert d.required is False
        assert d.description == ""
        assert d.default is None

    def test_custom(self) -> None:
        d = InputDef(required=True, description="A key", default="val")
        assert d.required is True
        assert d.description == "A key"
        assert d.default == "val"


class TestPipelineMeta:
    def test_minimal(self) -> None:
        m = PipelineMeta(name="test/x")
        assert m.name == "test/x"
        assert m.description == ""
        assert m.tags == []
        assert m.input == {}
        assert m.source == "local"

    def test_full(self) -> None:
        m = PipelineMeta(
            name="dummy/run",
            description="Does things",
            tags=["a", "b"],
            input={"x": InputDef(required=True)},
            source="builtin",
        )
        assert m.name == "dummy/run"
        assert len(m.tags) == 2
        assert m.input["x"].required is True
        assert m.source == "builtin"


class TestStepState:
    def test_defaults(self) -> None:
        s = StepState(id="browser_001", type="browser")
        assert s.status == "running"
        assert s.summary == ""
        assert s.error is None

    def test_failed(self) -> None:
        s = StepState(id="cmd_001", type="command", status="failed", error="timeout")
        assert s.status == "failed"
        assert s.error == "timeout"


class TestRunState:
    def test_initial(self) -> None:
        from pathlib import Path

        r = RunState(
            run_id="abc",
            pipeline_name="test/echo",
            work_dir=Path("/tmp/fake"),
        )
        assert r.status == "running"
        assert r.steps == []
        assert r.result is None
        assert r.cancel_requested is False

    def test_cancel(self) -> None:
        from pathlib import Path

        r = RunState(
            run_id="abc",
            pipeline_name="test/echo",
            work_dir=Path("/tmp/fake"),
        )
        r.cancel_requested = True
        assert r.cancel_requested is True
