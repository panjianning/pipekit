"""Tests for ArtifactStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipekit.artifact import ArtifactStore


class TestArtifactStore:
    def test_write_text(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        path = store.write_sync("data.txt", "hello world", fmt="text")
        assert (temp_dir / "data.txt").read_text() == "hello world"
        assert (temp_dir / "data.txt").resolve() == Path(path).resolve()

    def test_write_json(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        store.write_sync("data.json", {"x": 1})
        raw = (temp_dir / "data.json").read_text()
        assert json.loads(raw) == {"x": 1}

    def test_read_text(self, temp_dir: Path) -> None:
        (temp_dir / "msg.txt").write_text("hi")
        store = ArtifactStore(temp_dir)
        assert store.read_sync("msg.txt") == "hi"

    def test_read_json(self, temp_dir: Path) -> None:
        (temp_dir / "cfg.json").write_text('{"a":1}')
        store = ArtifactStore(temp_dir)
        assert store.read_sync("cfg.json") == {"a": 1}

    def test_nested_directory(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        store.write_sync("a/b/c.txt", "deep", fmt="text")
        assert (temp_dir / "a" / "b" / "c.txt").read_text() == "deep"

    def test_path_escape_blocked(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        with pytest.raises(ValueError, match="escapes work_dir"):
            store.write_sync("../outside.txt", "nope")

    def test_empty_path(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        with pytest.raises(ValueError, match="cannot be empty"):
            store.write_sync("  ", "x")

    def test_auto_format_json(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        store.write_sync("auto.json", {"key": [1, 2]})
        assert json.loads((temp_dir / "auto.json").read_text()) == {"key": [1, 2]}

    def test_read_nonexistent(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        with pytest.raises(FileNotFoundError):
            store.read_sync("missing.txt")

    def test_write_and_read_roundtrip(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        data = {"nested": {"deep": True}, "items": [1, 2, 3]}
        store.write_sync("roundtrip.json", data)
        result = store.read_sync("roundtrip.json")
        assert result == data

    def test_write_overwrites(self, temp_dir: Path) -> None:
        store = ArtifactStore(temp_dir)
        store.write_sync("dup.txt", "first", fmt="text")
        store.write_sync("dup.txt", "second", fmt="text")
        assert store.read_sync("dup.txt") == "second"
