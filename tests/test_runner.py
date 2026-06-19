"""Tests for PipelineRunner and PipelineExecutor."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipekit.pipeline.executor import PipelineExecutor
from pipekit.pipeline.runner import PipelineRunner
from pipekit.pipeline.types import PipelineMeta, RunState


def _fake_pipeline_fn(result: dict | None = None):
    """Factory for a pipeline run function that returns *result*."""
    if result is None:
        result = {"ok": True}

    async def run(ctx):
        ctx.log("running fake pipeline")
        return result

    return run


class TestPipelineExecutor:
    @pytest.mark.asyncio
    async def test_execute_success(self, temp_dir: Path, mock_browser_context) -> None:
        meta = PipelineMeta(name="test/success")
        run_fn = _fake_pipeline_fn({"count": 42})
        run = RunState(
            run_id="exec-1",
            pipeline_name="test/success",
            work_dir=temp_dir,
            input={"key": "val"},
        )

        executor = PipelineExecutor()
        await executor.execute(meta, run_fn, run, mock_browser_context)

        assert run.status == "completed"
        assert run.result == {"count": 42}
        assert (temp_dir / "result.json").exists()
        assert (temp_dir / "run.json").exists()

    @pytest.mark.asyncio
    async def test_execute_returns_non_dict(self, temp_dir: Path, mock_browser_context) -> None:
        meta = PipelineMeta(name="test/bad_return")
        async def bad_run(ctx):
            return "not a dict"

        run = RunState(
            run_id="exec-2",
            pipeline_name="test/bad_return",
            work_dir=temp_dir,
        )

        executor = PipelineExecutor()
        await executor.execute(meta, bad_run, run, mock_browser_context)

        assert run.status == "failed"

    @pytest.mark.asyncio
    async def test_execute_raises(self, temp_dir: Path, mock_browser_context) -> None:
        meta = PipelineMeta(name="test/crash")
        async def crash(ctx):
            raise ValueError("something broke")

        run = RunState(
            run_id="exec-3",
            pipeline_name="test/crash",
            work_dir=temp_dir,
        )

        executor = PipelineExecutor()
        await executor.execute(meta, crash, run, mock_browser_context)

        assert run.status == "failed"
        assert "something broke" in run.logs[-1]

    @pytest.mark.asyncio
    async def test_execute_canceled(self, temp_dir: Path, mock_browser_context) -> None:
        meta = PipelineMeta(name="test/cancel")
        async def slow(ctx):
            # Simulate cancel during execution
            pass

        run = RunState(
            run_id="exec-4",
            pipeline_name="test/cancel",
            work_dir=temp_dir,
        )
        run.cancel_requested = True

        executor = PipelineExecutor()
        await executor.execute(meta, slow, run, mock_browser_context)


class TestPipelineRunner:
    @pytest.mark.asyncio
    async def test_merge_input_defaults(self) -> None:
        runner = PipelineRunner()
        from pipekit.pipeline.types import InputDef

        meta = PipelineMeta(
            name="test/defaults",
            input={
                "name": InputDef(required=False, default="world"),
                "count": InputDef(required=False, default="10"),
            },
        )
        merged = runner._merge_input(meta, {"name": "pipekit"})
        assert merged["name"] == "pipekit"
        assert merged["count"] == "10"

    @pytest.mark.asyncio
    async def test_merge_input_missing_required(self) -> None:
        runner = PipelineRunner()
        from pipekit.pipeline.types import InputDef

        meta = PipelineMeta(
            name="test/required",
            input={"key": InputDef(required=True, description="A required key")},
        )
        with pytest.raises(ValueError, match="missing required input"):
            runner._merge_input(meta, {})

    @pytest.mark.asyncio
    async def test_run_by_name(self, temp_dir: Path, mock_browser_session) -> None:
        runner = PipelineRunner(working_dir=temp_dir)
        # sqlite/upsert is built-in, doesn't need browser
        run = await runner.run_by_name(
            "sqlite/upsert",
            {
                "table": "runner_test",
                "rows": [{"id": 1, "val": "test"}],
                "unique_keys": ["id"],
            },
            mock_browser_session,
        )
        assert run.status == "completed"
        assert run.result is not None
        assert run.result["ok"] is True
        assert run.result["table"] == "runner_test"
        assert run.result["unique_keys"] == ["id"]

    @pytest.mark.asyncio
    async def test_run_by_path(self, temp_dir: Path, mock_browser_session) -> None:
        file_path = temp_dir / "adhoc.pipeline.py"
        file_path.write_text("""meta = {"name": "adhoc/test"}
async def run(ctx):
    ctx.log("adhoc run")
    return {"done": True}
""")
        runner = PipelineRunner(working_dir=temp_dir)
        run = await runner.run_by_path(file_path, {}, mock_browser_session)
        assert run.status == "completed"
        assert run.result == {"done": True}

    def test_list_pipelines(self, temp_dir: Path) -> None:
        runner = PipelineRunner(working_dir=temp_dir)
        pipelines = runner.list_pipelines()
        names = [p.name for p in pipelines]
        assert "sqlite/upsert" in names
        assert "mongo/upsert" in names

    def test_get_pipeline_info(self, temp_dir: Path) -> None:
        runner = PipelineRunner(working_dir=temp_dir)
        meta = runner.get_pipeline("sqlite/upsert")
        assert meta is not None
        assert meta.name == "sqlite/upsert"
        assert meta.description != ""
