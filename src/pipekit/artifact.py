from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    """Read/write pipeline artifacts within a sandboxed work directory.

    All paths are relative to the run's work_dir. Path traversal outside
    work_dir is blocked.
    """

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir.resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)

    async def write(
        self,
        relative_path: str,
        data: Any,
        fmt: str | None = None,
    ) -> str:
        """Write data to *relative_path* (json or text). Returns the absolute path."""
        return self.write_sync(relative_path, data, fmt)

    def write_sync(
        self,
        relative_path: str,
        data: Any,
        fmt: str | None = None,
    ) -> str:
        """Synchronous version of :meth:`write`."""
        target = self._resolve(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        mode = fmt or ("json" if not isinstance(data, str) else "text")
        if mode == "text":
            payload = str(data)
        elif mode == "json":
            payload = json.dumps(data, ensure_ascii=False, indent=2)
        else:
            raise ValueError(f"Unsupported artifact format: {mode}")

        target.write_text(payload, encoding="utf-8")
        return str(target)

    async def read(self, relative_path: str, fmt: str | None = None) -> Any:
        """Read data from *relative_path*. Format auto-detected by extension."""
        return self.read_sync(relative_path, fmt)

    def read_sync(self, relative_path: str, fmt: str | None = None) -> Any:
        """Synchronous version of :meth:`read`."""
        target = self._resolve(relative_path)
        raw = target.read_text(encoding="utf-8")

        mode = fmt or ("json" if target.suffix.lower() == ".json" else "text")
        if mode == "text":
            return raw
        if mode == "json":
            return json.loads(raw)
        raise ValueError(f"Unsupported artifact format: {mode}")

    def _resolve(self, relative_path: str) -> Path:
        clean = relative_path.strip()
        if not clean:
            raise ValueError("Artifact path cannot be empty")
        target = (self.work_dir / clean).resolve()
        # Ensure the resolved path is within work_dir
        try:
            target.relative_to(self.work_dir)
        except ValueError as exc:
            raise ValueError(f"Artifact path escapes work_dir: {relative_path}") from exc
        return target
