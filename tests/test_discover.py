"""Tests for PipelineDiscover."""

from __future__ import annotations

from pathlib import Path

from pipekit.pipeline.discover import PipelineDiscover


def _write_pipeline(dir_path: Path, name: str, content: str | None = None) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    if content is None:
        content = (
            f'meta = {{"name": "{name}"}}\n'
            "async def run(ctx):\n"
            "    return {}\n"
        )
    (dir_path / f"{name.replace('/', '_')}.pipeline.py").write_text(content)


class TestPipelineDiscover:
    def test_discover_empty(self, temp_dir: Path) -> None:
        discover = PipelineDiscover(working_dir=temp_dir)
        result = discover.discover_all()
        names = [p.name for p in result]
        assert "sqlite/upsert" in names
        assert "mongo/upsert" in names

    def test_discover_project_local(self, temp_dir: Path) -> None:
        """Project-local pipelines override builtins."""
        local_dir = temp_dir / ".pipekit" / "pipelines"
        content = (
            'meta = {"name": "sqlite/upsert",'
            ' "description": "OVERRIDDEN"}\n'
            "async def run(ctx):\n"
            '    return {"local": True}\n'
        )
        _write_pipeline(local_dir, "sqlite/upsert", content)

        discover = PipelineDiscover(working_dir=temp_dir)
        all_pipelines = discover.discover_all()

        sqlite = next(p for p in all_pipelines if p.name == "sqlite/upsert")
        assert sqlite.description == "OVERRIDDEN"
        assert sqlite.source == "local"

    def test_resolve_by_name(self) -> None:
        discover = PipelineDiscover()
        result = discover.resolve("sqlite/upsert")
        assert result is not None
        meta, run_fn = result
        assert meta.name == "sqlite/upsert"
        assert callable(run_fn)

    def test_resolve_not_found(self, temp_dir: Path) -> None:
        discover = PipelineDiscover(working_dir=temp_dir)
        assert discover.resolve("nonexistent/pipe") is None

    def test_resolve_file(self, temp_dir: Path) -> None:
        file_path = temp_dir / "custom.pipeline.py"
        file_path.write_text(
            'meta = {"name": "custom/one", "description": "A custom pipeline"}\n'
            "async def run(ctx):\n"
            '    return {"ok": True}\n'
        )
        discover = PipelineDiscover(working_dir=temp_dir)
        meta, run_fn = discover.resolve_file(file_path)
        assert meta.name == "custom/one"
        assert meta.description == "A custom pipeline"

    def test_resolve_file_not_found(self, temp_dir: Path) -> None:
        discover = PipelineDiscover(working_dir=temp_dir)
        import pytest

        with pytest.raises(FileNotFoundError):
            discover.resolve_file(temp_dir / "does_not_exist.pipeline.py")

    def test_pipekit_home_env(self, temp_dir: Path, monkeypatch) -> None:
        home_dir = temp_dir / "custom_home"
        local_dir = home_dir / "pipelines" / "local"
        _write_pipeline(local_dir, "from/home")

        monkeypatch.setenv("PIPEKIT_HOME", str(home_dir))
        discover = PipelineDiscover(working_dir=temp_dir)
        all_pipelines = discover.discover_all()
        names = [p.name for p in all_pipelines]
        assert "from/home" in names

    def test_discover_all_sorted(self) -> None:
        discover = PipelineDiscover()
        result = discover.discover_all()
        names = [p.name for p in result]
        assert names == sorted(names)
