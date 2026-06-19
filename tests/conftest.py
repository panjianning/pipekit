"""Shared fixtures for PipeKit tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory that auto-cleans up."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def mock_browser_context():
    """Mock Playwright BrowserContext."""
    ctx = MagicMock()
    ctx.new_page = AsyncMock()
    ctx.storage_state = AsyncMock(return_value={"cookies": [], "origins": []})
    ctx.close = AsyncMock()
    return ctx


@pytest.fixture
def mock_browser_session(mock_browser_context):
    """Mock BrowserSession that returns a fake isolated context."""
    session = MagicMock()
    session.isolate_with_login = AsyncMock(return_value=mock_browser_context)
    session.ensure_browser = AsyncMock()
    session.close = AsyncMock()
    session.idle_seconds = 0.0
    return session


@pytest.fixture
def sample_pipeline_meta():
    """Minimal PipelineMeta for testing."""
    from pipekit.pipeline.types import InputDef, PipelineMeta

    return PipelineMeta(
        name="test/echo",
        description="A test pipeline",
        input={
            "message": InputDef(required=False, description="Test message", default="hello"),
        },
    )
