"""Browser session management — one Chromium process per account.

Managed mode: launches Chrome via subprocess, connects via CDP.
CDP mode: connects to an existing Chrome via connect_over_cdp.
Task isolation: clone storageState from master → newContext().
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
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


def _kill_chrome_with_profile(user_data_dir: str) -> None:
    """Kill any Chrome processes using the given user-data-dir.

    Without this, a new Chrome launch with the same profile will silently
    open in the existing window, ignoring --remote-debugging-port.
    """
    profile = os.path.realpath(user_data_dir)
    for pid in _find_chrome_pids():
        try:
            cmdline = _read_proc_cmdline(pid)
            if cmdline and profile in cmdline:
                logger.info("Killing stale Chrome pid=%d using %s", pid, profile)
                os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass


def _find_chrome_pids() -> list[int]:
    """Return PIDs of running Chrome/Chromium processes."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "Google Chrome"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return [int(pid) for pid in result.stdout.strip().split("\n") if pid]
    except Exception:
        pass
    return []


def _read_proc_cmdline(pid: int) -> str | None:
    """Read the command line of a process by PID."""
    try:
        import subprocess as _sp
        result = _sp.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _allocate_port(account_name: str) -> int:
    """Find a free TCP port for Chrome remote debugging.

    First tries to reuse the last-used port (stored on disk).
    If that fails, finds a free port and persists it.
    """
    import socket

    port_file = _pipekit_root() / "run" / f"chrome-{account_name}.port"

    # Try last-used port first
    if port_file.exists():
        try:
            last_port = int(port_file.read_text().strip())
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", last_port))
                return last_port
        except (ValueError, OSError):
            pass  # port in use or invalid, find a new one

    # Find a free port
    base = 19970
    offset = abs(hash(account_name)) % 100
    for attempt in range(20):
        port = base + offset + attempt
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                port_file.parent.mkdir(parents=True, exist_ok=True)
                port_file.write_text(str(port))
                return port
            except OSError:
                continue

    # Last resort: let OS pick
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(port))
        return port


# ---------------------------------------------------------------------------
# BrowserSession
# ---------------------------------------------------------------------------


class BrowserSession:
    """One browser process per account.

    Launches Chrome directly as a subprocess (not via Playwright's managed
    launch) to avoid bot detection. Connects via CDP for full control.

    CDP mode (--cdp <port>):
        connect_over_cdp → use the first existing context as master.
    """

    def __init__(self, account_name: str = "default", cdp_target: str | None = None) -> None:
        self.account_name = account_name
        self.cdp_target = cdp_target
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._master_context: BrowserContext | None = None
        self._last_used: float = 0.0
        self._chrome_proc: asyncio.subprocess.Process | None = None
        self._debug_port: int = 0

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
        """Close the browser gracefully, preserving cookies to disk."""
        # Try graceful CDP shutdown first (flushes cookies)
        if self._browser and self._browser.is_connected():
            try:
                await self._browser.close()
            except Exception:
                logger.debug("Error closing browser via CDP", exc_info=True)
        self._browser = None
        self._master_context = None

        # Wait a moment for Chrome to flush to disk, then terminate
        if self._chrome_proc is not None:
            try:
                await asyncio.wait_for(self._chrome_proc.wait(), timeout=3)
            except TimeoutError:
                self._chrome_proc.terminate()
                try:
                    await asyncio.wait_for(self._chrome_proc.wait(), timeout=2)
                except TimeoutError:
                    self._chrome_proc.kill()
                    await self._chrome_proc.wait()
            except Exception:
                logger.debug("Error waiting for chrome", exc_info=True)
            self._chrome_proc = None

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
        contexts = browser.contexts
        if contexts:
            self._master_context = contexts[0]
        else:
            self._master_context = await browser.new_context()
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
        """Launch Chrome via subprocess, then connect via CDP.

        Avoids Playwright's managed launch which adds --enable-automation
        and other flags that trigger bot detection.
        """
        assert self._playwright is not None

        user_data = str(_profile_dir(self.account_name))
        chrome_bin = self._find_chrome()
        self._debug_port = _allocate_port(self.account_name)

        # Kill any previous Chrome using our profile, otherwise Chrome
        # reuses the existing window and ignores our --remote-debugging-port
        _kill_chrome_with_profile(user_data)

        # Flags for a natural-looking Chrome: no automation indicators
        args = [
            chrome_bin,
            f"--remote-debugging-port={self._debug_port}",
            f"--user-data-dir={user_data}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=ChromeWhatsNewUI",
            "--disable-dev-shm-usage",
            "--window-size=1440,900",
        ]

        logger.info(
            "Launching Chrome %s profile=%s port=%d",
            chrome_bin, user_data, self._debug_port,
        )

        self._chrome_proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Wait for Chrome to be ready on the debug port
        await self._connect_cdp(f"127.0.0.1:{self._debug_port}")

    async def _start_cdp(self) -> None:
        """Connect to an existing Chrome via CDP."""
        assert self._playwright is not None
        assert self.cdp_target is not None
        await self._connect_cdp(self.cdp_target)

    async def _connect_cdp(self, target: str) -> None:
        """Connect to Chrome at *target* (e.g. '127.0.0.1:9222'), retrying."""
        assert self._playwright is not None

        endpoints = await self._resolve_cdp_endpoints(target)
        last_error: Exception | None = None

        for attempt in range(30):  # up to 15 seconds
            for ep in endpoints:
                try:
                    self._browser = await self._playwright.chromium.connect_over_cdp(ep)
                    logger.info("Connected CDP target=%s", target)
                    return
                except Exception as exc:
                    last_error = exc
                    logger.debug("CDP attempt %d for %s failed", attempt, ep)
            await asyncio.sleep(0.5)

        raise RuntimeError(
            f"Failed to connect to Chrome CDP at {target}: {last_error}"
        )

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
