"""Dynamic loading of .pipeline.py modules."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from .types import InputDef, OutputDef, PipelineMeta


class PipelineLoader:
    """Load a .pipeline.py file and extract its meta + run function."""

    def load(self, file_path: Path) -> tuple[PipelineMeta, Any]:
        """Load a pipeline module. Returns (meta, run_fn).``"""
        module = self._import_file(file_path)
        raw_meta: dict[str, Any] = getattr(module, "meta", None) or {}

        run_fn = getattr(module, "run", None)
        if run_fn is None:
            raise ValueError(f"Pipeline module missing async def run(ctx): {file_path}")

        if not callable(run_fn):
            raise ValueError(f"Pipeline 'run' must be an async function: {file_path}")

        name = str(raw_meta.get("name", "")).strip()
        if not name:
            raise ValueError(f"Pipeline meta.name is required: {file_path}")

        description = str(raw_meta.get("description", ""))
        tags = list(raw_meta.get("tags", [])) if isinstance(raw_meta.get("tags"), list) else []
        tags = [str(t) for t in tags]

        input_defs: dict[str, InputDef] = {}
        for key, raw in (raw_meta.get("input") or {}).items():
            if isinstance(raw, dict):
                input_defs[str(key)] = InputDef(
                    required=bool(raw.get("required", False)),
                    description=str(raw.get("description", "")),
                    default=raw.get("default"),
                )

        output_defs: dict[str, OutputDef] = {}
        for key, raw in (raw_meta.get("output") or {}).items():
            if isinstance(raw, dict):
                output_defs[str(key)] = OutputDef(
                    type=str(raw.get("type", "any")),
                    description=str(raw.get("description", "")),
                )

        meta = PipelineMeta(
            name=name,
            description=description,
            tags=tags,
            input=input_defs,
            output=output_defs,
            file_path=file_path,
            source="local",
        )
        return meta, run_fn

    @staticmethod
    def _import_file(file_path: Path) -> Any:
        """Import a Python file as a module by path."""
        module_name = f"pipekit_pipeline_{abs(hash(str(file_path)))}"
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load module: {file_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
