"""Tests for PipelineLoader."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipekit.pipeline.loader import PipelineLoader


class TestPipelineLoader:
    def test_load_valid_pipeline(self, temp_dir: Path) -> None:
        file_path = temp_dir / "hello.pipeline.py"
        file_path.write_text("""meta = {"name": "test/hello", "description": "Say hi"}
async def run(ctx):
    return {"msg": ctx.input.get("message", "default")}
""")
        loader = PipelineLoader()
        meta, run_fn = loader.load(file_path)
        assert meta.name == "test/hello"
        assert meta.description == "Say hi"

    def test_load_missing_run(self, temp_dir: Path) -> None:
        file_path = temp_dir / "bad.pipeline.py"
        file_path.write_text('meta = {"name": "bad/noop"}')
        loader = PipelineLoader()
        with pytest.raises(ValueError, match="missing async def run"):
            loader.load(file_path)

    def test_load_missing_name(self, temp_dir: Path) -> None:
        file_path = temp_dir / "noname.pipeline.py"
        file_path.write_text("""async def run(ctx):
    return {}
""")
        loader = PipelineLoader()
        with pytest.raises(ValueError, match="meta.name is required"):
            loader.load(file_path)

    def test_load_with_input_defs(self, temp_dir: Path) -> None:
        file_path = temp_dir / "with_input.pipeline.py"
        file_path.write_text("""meta = {
    "name": "test/with_input",
    "input": {
        "keyword": {"required": True, "description": "Search keyword"},
        "limit": {"required": False, "default": "20"},
    },
}
async def run(ctx):
    return {"keyword": ctx.input["keyword"]}
""")
        loader = PipelineLoader()
        meta, _ = loader.load(file_path)
        assert meta.input["keyword"].required is True
        assert meta.input["keyword"].description == "Search keyword"
        assert meta.input["limit"].required is False
        assert meta.input["limit"].default == "20"

    def test_load_with_tags(self, temp_dir: Path) -> None:
        file_path = temp_dir / "tagged.pipeline.py"
        file_path.write_text("""meta = {"name": "test/tagged", "tags": ["db", "sqlite"]}
async def run(ctx):
    return {}
""")
        loader = PipelineLoader()
        meta, _ = loader.load(file_path)
        assert meta.tags == ["db", "sqlite"]

    def test_load_with_output_defs(self, temp_dir: Path) -> None:
        file_path = temp_dir / "with_output.pipeline.py"
        file_path.write_text("""meta = {
    "name": "test/output",
    "output": {
        "count": {"type": "int", "description": "Number of results"},
    },
}
async def run(ctx):
    return {"count": 10}
""")
        loader = PipelineLoader()
        meta, _ = loader.load(file_path)
        assert meta.output["count"].type == "int"
        assert meta.output["count"].description == "Number of results"
