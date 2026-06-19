"""Browser session management — one Chromium process per account.

Key design:
- Managed mode: Playwright launches Chrome via launch_persistent_context
- CDP mode: connects to existing Chrome via connect_over_cdp
- Task isolation: clone storageState from master → newContext()
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]

try:
    from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
except ImportError:
    Browser = Any  # type: ignore[assignment,misc]
    BrowserContext = Any  # type: ignore[assignment,misc]
    Playwright = Any  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_home_env = "PIPEKIT_HOME"


def _pipekit_root() -> Path:
    """Resolve PipeKit home directory. Default: ~/.pipekit"""
    env = os.environ.get(_home_env, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".pipekit"


def _profile_dir(account_name: str) -> Path:
    return _pipekit_root() / "profiles" / account_name


# ---------------------------------------------------------------------------
# BrowserSession
# ---------------------------------------------------------------------------


class BrowserSession:
    """One browser process per account.

    Managed mode (no --cdp):
        launch_persistent_context(user_data_dir=profiles/<name>)
        → master context is the persistent one
        → login cookies survive restarts

    CDP mode (--cdp <port>):
        connect_over_cdp → use the first existing context as master
    """

    def __init__(self, account_name: str = "default", cdp_target: str | None = None) -> None:
        self.account_name = account_name
        self.cdp_target = cdp_target
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._master_context: BrowserContext | None = None
        self._last_used: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def ensure_browser(self) -> Browser:
        """Return a connected browser, launching one if necessary."""
        self._last_used = time.monotonic()
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        await self._start_browser()
        if self._browser is None:
            raise RuntimeError("Browser failed to start")
        return self._browser

    async def close(self) -> None:
        """Close the browser and stop Playwright."""
        if self._browser and self._browser.is_connected():
            try:
                await self._browser.close()
            except Exception:
                logger.debug("Error closing browser", exc_info=True)
        self._browser = None
        self._master_context = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("Error stopping playwright", exc_info=True)
            self._playwright = None

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    async def get_master_context(self) -> BrowserContext:
        """Return the persistent master context (with login cookies)."""
        if self._master_context is not None:
            return self._master_context
        browser = await self.ensure_browser()
        if self.cdp_target:
            contexts = browser.contexts
            self._master_context = contexts[0] if contexts else await browser.new_context()
        # managed: _start_browser already set _master_context via launch_persistent_context
        if self._master_context is None:
            raise RuntimeError("No master context available")
        return self._master_context

    async def isolate_with_login(self) -> BrowserContext:
        """Create a new isolated context cloned from master's storage state.

        The new context has the same cookies/localStorage as master but its own
        tab/cache/storage namespace. Use this for each pipeline run to prevent
        cross-run tab pollution.
        """
        master = await self.get_master_context()
        state = await master.storage_state()
        browser = await self.ensure_browser()
        return await browser.new_context(storage_state=state)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _start_browser(self) -> None:
        if async_playwright is None:
            raise RuntimeError("playwright is not installed; run: pip install playwright")

        self._playwright = await async_playwright().start()

        if self.cdp_target:
            await self._start_cdp()
        else:
            await self._start_managed()

    async def _start_managed(self) -> None:
        """Launch Chrome with a persistent profile so login cookies survive restart."""
        assert self._playwright is not None

        user_data = str(_profile_dir(self.account_name))
        chrome_bin = self._find_chrome()

        logger.info("Launching managed Chrome profile=%s", user_data)
        self._master_context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=user_data,
            headless=False,
            executable_path=chrome_bin,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        self._browser = self._master_context.browser

    async def _start_cdp(self) -> None:
        """Connect to an existing Chrome via CDP."""
        assert self._playwright is not None
        assert self.cdp_target is not None

        endpoints = await self._resolve_cdp_endpoints(self.cdp_target)
        for ep in endpoints:
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(ep)
                logger.info("Connected CDP %s", self.cdp_target)
                return
            except Exception:
                logger.debug("CDP endpoint %s failed", ep)
        raise RuntimeError(f"Failed to connect to CDP target: {self.cdp_target}")

    # ------------------------------------------------------------------
    # CDP endpoint resolution
    # ------------------------------------------------------------------

    async def _resolve_cdp_endpoints(self, raw: str) -> list[str]:
        value = raw.strip()
        if not value:
            return []
        if value.isdigit():
            return await self._expand_http(f"http://127.0.0.1:{value}")

        parsed = urlparse(value)
        if parsed.scheme in ("http", "https"):
            return await self._expand_http(value)
        if parsed.scheme in ("ws", "wss"):
            path = parsed.path or ""
            if not path or path == "/":
                host = parsed.hostname or "127.0.0.1"
                port = parsed.port or 9222
                scheme = "https" if parsed.scheme == "wss" else "http"
                discovered = await self._expand_http(f"{scheme}://{host}:{port}")
                return discovered + [value]
            return [value]
        # e.g. "localhost:9222"
        if "://" not in value and ":" in value and "/" not in value:
            return await self._expand_http(f"http://{value}")
        return [value]

    async def _expand_http(self, base: str) -> list[str]:
        parsed = urlparse(base)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9222
        ws_scheme = "wss" if scheme == "https" else "ws"
        normalized = f"{scheme}://{host}:{port}"

        out: list[str] = [normalized, f"{ws_scheme}://{host}:{port}/devtools/browser"]
        discovered = await self._discover_ws(normalized)
        out.extend(discovered)

        # Deduplicate while preserving order
        seen: set[str] = set()
        dedup: list[str] = []
        for item in out:
            if item not in seen:
                seen.add(item)
                dedup.append(item)
        return dedup

    async def _discover_ws(self, http_base: str) -> list[str]:
        """Try to discover WebSocket endpoints from CDP HTTP API."""
        urls: list[str] = []
        try:
            urls = await self._fetch_cdp_endpoints(http_base)
        except Exception:
            logger.debug("CDP discovery failed for %s", http_base)

        return urls

    async def _fetch_cdp_endpoints(self, http_base: str) -> list[str]:
        """Fetch WS endpoints from CDP /json/version and /json/list."""
        import urllib.request

        out: list[str] = []

        def _fetch(url: str) -> Any:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))

        try:
            version = _fetch(f"{http_base}/json/version")
            if isinstance(version, dict):
                ws = version.get("webSocketDebuggerUrl")
                if isinstance(ws, str) and ws.strip():
                    out.append(ws.strip())
        except Exception:
            pass

        try:
            targets = _fetch(f"{http_base}/json/list")
            if isinstance(targets, list):
                for item in targets:
                    if isinstance(item, dict):
                        ws = item.get("webSocketDebuggerUrl")
                        if isinstance(ws, str) and ws.strip():
                            out.append(ws.strip())
        except Exception:
            pass

        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_chrome() -> str:
        """Locate a usable Chrome/Chromium binary on the system."""
        candidates: list[str] = []

        chrome_names = [
            "google-chrome", "google-chrome-stable",
            "chromium", "chromium-browser", "chrome",
        ]
        for name in chrome_names:
            found = shutil.which(name)
            if found:
                candidates.append(found)

        if sys.platform == "darwin":
            candidates.extend([
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ])
        elif sys.platform.startswith("linux"):
            candidates.extend([
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
                "/snap/bin/chromium",
            ])

        for path in candidates:
            if Path(path).exists():
                return path

        raise RuntimeError(
            "Chrome binary not found. Install Chrome or set CHROME_PATH. "
            f"Checked: {', '.join(candidates[:5])}"
        )

    @staticmethod
    async def close_context_safe(ctx: BrowserContext) -> None:
        """Close a browser context, swallowing any errors."""
        try:
            await ctx.close()
        except Exception:
            logger.debug("Error closing browser context", exc_info=True)
