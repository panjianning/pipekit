"""Multi-source pipeline discovery.

Priority (highest first):
1. Project-local   — <cwd>/.pipekit/pipelines/
2. User-local      — ~/.pipekit/pipelines/local/
3. Community       — ~/.pipekit/pipelines/community/
4. Built-in        — <site-packages>/pipekit/pipelines/
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .loader import PipelineLoader
from .types import PipelineMeta


def _pipekit_home() -> Path:
    env = os.environ.get("PIPEKIT_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".pipekit"


def _builtin_dir() -> Path:
    """Return the directory where built-in pipelines are installed."""
    return Path(__file__).resolve().parent.parent / "pipelines"


class PipelineDiscover:
    """Scan multiple directories for .pipeline.py files."""

    def __init__(self, working_dir: str | Path | None = None) -> None:
        self._loader = PipelineLoader()
        self._working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()

    def discover_all(self) -> list[PipelineMeta]:
        """Return all discoverable pipelines, deduplicated by name.

        Precedence: project-local > user-local > community > built-in.
        """
        sources: list[tuple[Path, str]] = [
            (_builtin_dir(), "builtin"),
            (_pipekit_home() / "pipelines" / "community", "community"),
            (_pipekit_home() / "pipelines" / "local", "local"),
            (self._working_dir / ".pipekit" / "pipelines", "local"),
        ]

        by_name: dict[str, PipelineMeta] = {}
        for directory, source in sources:
            for meta in self._scan(directory, source):
                by_name[meta.name] = meta

        return sorted(by_name.values(), key=lambda m: m.name)

    def resolve(self, name: str) -> tuple[PipelineMeta, Any] | None:
        """Find a pipeline by name and return (meta, run_fn), or None."""
        name = name.strip()
        if not name:
            return None

        sources: list[tuple[Path, str]] = [
            (self._working_dir / ".pipekit" / "pipelines", "local"),
            (_pipekit_home() / "pipelines" / "local", "local"),
            (_pipekit_home() / "pipelines" / "community", "community"),
            (_builtin_dir(), "builtin"),
        ]

        for directory, source in sources:
            if not directory.exists():
                continue
            for file_path in sorted(directory.rglob("*.pipeline.py")):
                try:
                    meta, run_fn = self._loader.load(file_path)
                    if meta.name == name:
                        meta.source = source
                        return meta, run_fn
                except Exception:
                    continue
        return None

    def resolve_file(self, file_path: str | Path) -> tuple[PipelineMeta, Any]:
        """Load a pipeline from an explicit file path."""
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Pipeline file not found: {path}")
        if not path.suffix == ".py" or not path.name.endswith(".pipeline.py"):
            raise ValueError(f"Not a .pipeline.py file: {path}")
        return self._loader.load(path)

    @staticmethod
    def _scan(directory: Path, source: str) -> list[PipelineMeta]:
        if not directory.exists():
            return []

        loader = PipelineLoader()
        results: list[PipelineMeta] = []
        for file_path in sorted(directory.rglob("*.pipeline.py")):
            try:
                meta, _run_fn = loader.load(file_path)
                meta.source = source
                results.append(meta)
            except Exception:
                continue
        return results
