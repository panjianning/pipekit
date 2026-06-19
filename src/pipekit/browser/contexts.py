"""Named browser context management.

Each context is an isolated BrowserContext cloned from an account's
master context (carries login cookies). Contexts are keyed by name
and auto-cleaned after idle timeout.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .session import BrowserSession

logger = logging.getLogger(__name__)


class ContextStore:
    """Manage named browser contexts across accounts."""

    IDLE_TIMEOUT = 600  # 10 minutes

    def __init__(self) -> None:
        self._store: dict[str, _ContextEntry] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def create(
        self, name: str, session: BrowserSession
    ) -> dict[str, Any]:
        """Create a named context cloned from *session*'s master."""
        name = name.strip()
        if not name:
            raise ValueError("Context name cannot be empty")
        if name in self._store:
            raise ValueError(f"Context '{name}' already exists")

        ctx = await session.isolate_with_login()
        entry = _ContextEntry(name=name, context=ctx, session=session)
        self._store[name] = entry

        logger.info("Created context '%s'", name)
        return {"ok": True, "name": name, "account": session.account_name}

    def list(self) -> list[dict[str, Any]]:
        """Return all active contexts with idle times."""
        now = time.monotonic()
        return [
            {
                "name": e.name,
                "account": e.session.account_name,
                "idle_seconds": round(now - e.last_used, 1),
                "page_count": len(e.pages),
            }
            for e in self._store.values()
        ]

    def get(self, name: str) -> Any:
        """Return the BrowserContext for *name*, or raise ValueError."""
        name = name.strip()
        if name not in self._store:
            raise ValueError(f"Context '{name}' not found")
        entry = self._store[name]
        entry.last_used = time.monotonic()
        return entry

    async def destroy(self, name: str) -> dict[str, Any]:
        """Close and remove a named context."""
        name = name.strip()
        entry = self._store.pop(name, None)
        if entry is None:
            raise ValueError(f"Context '{name}' not found")
        await BrowserSession.close_context_safe(entry.context)
        logger.info("Destroyed context '%s'", name)
        return {"ok": True, "name": name}

    async def destroy_all(self) -> None:
        """Close all contexts (used on daemon shutdown)."""
        for entry in list(self._store.values()):
            await BrowserSession.close_context_safe(entry.context)
        self._store.clear()

    async def cleanup_idle(self) -> None:
        """Close contexts that exceed idle timeout."""
        now = time.monotonic()
        stale = [
            name
            for name, entry in self._store.items()
            if now - entry.last_used > self.IDLE_TIMEOUT
        ]
        for name in stale:
            try:
                await self.destroy(name)
            except Exception:
                logger.debug("Error cleaning context '%s'", name)


class _ContextEntry:
    __slots__ = ("name", "context", "session", "last_used", "pages")

    def __init__(self, name: str, context: Any, session: BrowserSession) -> None:
        self.name = name
        self.context = context
        self.session = session
        self.last_used = time.monotonic()
        self.pages: list[Any] = []  # Playwright pages opened in this context
