from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class InputDef:
    """Pipeline input parameter definition."""

    required: bool = False
    description: str = ""
    default: Any = None


@dataclass(slots=True)
class OutputDef:
    """Pipeline output field definition."""

    type: str = "any"
    description: str = ""


@dataclass(slots=True)
class PipelineMeta:
    """Discovered pipeline metadata."""

    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    input: dict[str, InputDef] = field(default_factory=dict)
    output: dict[str, OutputDef] = field(default_factory=dict)
    file_path: Path | None = None
    source: str = "local"  # "builtin" | "local" | "community"


@dataclass(slots=True)
class StepState:
    """Record of one step within a pipeline run."""

    id: str
    type: str  # "browser" | "pipeline" | "artifact" | "log" | "command"
    status: str = "running"  # "pending" | "running" | "completed" | "failed" | "canceled"
    started_at: float = 0.0
    ended_at: float | None = None
    summary: str = ""
    error: str | None = None
    exit_code: int | None = None
    artifact_path: str | None = None


@dataclass
class RunState:
    """Full state of a single pipeline run."""

    run_id: str
    pipeline_name: str
    work_dir: Path
    status: str = "running"  # "running" | "completed" | "failed" | "canceled"
    started_at: float = 0.0
    ended_at: float | None = None
    input: dict[str, Any] = field(default_factory=dict)
    steps: list[StepState] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_requested: bool = False
