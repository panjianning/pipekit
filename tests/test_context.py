"""Tests for PipelineContext."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pipekit.pipeline.context import PipelineContext
from pipekit.pipeline.step import StepManager
from pipekit.pipeline.types import RunState


def _make_run(work_dir: Path) -> RunState:
    return RunState(
        run_id="ctx-test",
        pipeline_name="test/echo",
        work_dir=work_dir,
        input={"message": "hi"},
    )


class TestPipelineContext:
    def test_input_access(self, temp_dir: Path, mock_browser_context) -> None:
        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={"key": "value", "num": 42},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        assert ctx.input == {"key": "value", "num": 42}

    def test_log(self, temp_dir: Path, mock_browser_context) -> None:
        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        ctx.log("hello world")
        assert any("hello world" in line for line in run.logs)

    def test_artifact_write_read(self, temp_dir: Path, mock_browser_context) -> None:
        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        ctx.artifact.write_sync("test.json", {"a": 1})
        data = ctx.artifact.read_sync("test.json")
        assert data == {"a": 1}

    @pytest.mark.asyncio
    async def test_browser_navigate(self, temp_dir: Path, mock_browser_context) -> None:
        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_browser_context.new_page.return_value = mock_page

        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        await ctx.browser.navigate("https://example.com")
        mock_browser_context.new_page.assert_called_once()
        mock_page.goto.assert_called_once_with("https://example.com", wait_until="domcontentloaded")

    @pytest.mark.asyncio
    async def test_browser_evaluate(self, temp_dir: Path, mock_browser_context) -> None:
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock(return_value={"result": "ok"})
        mock_page.close = AsyncMock()
        mock_browser_context.new_page.return_value = mock_page

        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        result = await ctx.browser.evaluate("1 + 1")
        assert result == {"result": "ok"}
        mock_page.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_sub_call(self, temp_dir: Path, mock_browser_context) -> None:
        def fake_resolve(name):
            return (MagicMock(name="sub_meta"), AsyncMock(return_value={"sub": "ok"}))

        async def fake_execute(loaded, sub_input):
            _, run_fn = loaded
            return await run_fn({})

        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
            pipeline_resolver=fake_resolve,
            pipeline_executor=fake_execute,
        )
        result = await ctx.pipeline.run("sub/test")
        assert result == {"sub": "ok"}

    @pytest.mark.asyncio
    async def test_pipeline_sub_call_not_found(self, temp_dir: Path, mock_browser_context) -> None:
        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
            pipeline_resolver=lambda _: None,
        )
        with pytest.raises(ValueError, match="not found"):
            await ctx.pipeline.run("missing/pipe")

    @pytest.mark.asyncio
    async def test_utils_run_command(self, temp_dir: Path, mock_browser_context) -> None:
        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        result = await ctx.utils.run_command("echo", ["hello"])
        assert result["ok"] is True
        assert "hello" in result["stdout"]
        assert result["status"] == 0

    def test_utils_resolve_path(self, temp_dir: Path, mock_browser_context) -> None:
        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        resolved = ctx.utils.resolve_path("sub/file.txt")
        assert Path(resolved).resolve() == (temp_dir / "sub" / "file.txt").resolve()

    def test_utils_read_write_text(self, temp_dir: Path, mock_browser_context) -> None:
        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        ctx.utils.write_text("note.txt", "hello utils")
        assert ctx.utils.read_text("note.txt") == "hello utils"

    def test_utils_read_write_json(self, temp_dir: Path, mock_browser_context) -> None:
        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        ctx.utils.write_json("data.json", {"items": [1, 2, 3]})
        assert ctx.utils.read_json("data.json") == {"items": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_new_page(self, temp_dir: Path, mock_browser_context) -> None:
        mock_page = MagicMock()
        mock_browser_context.new_page.return_value = mock_page

        steps = StepManager()
        run = _make_run(temp_dir)
        ctx = PipelineContext(
            input_data={},
            work_dir=temp_dir,
            browser_context=mock_browser_context,
            steps=steps,
            run=run,
        )
        page = await ctx.new_page()
        assert page is mock_page
