"""Execute atomic browser actions on a page within a context."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any


async def navigate(context: Any, url: str) -> dict[str, Any]:
    """Navigate to *url*, reuse existing page or create new one."""
    entry = context  # ContextStore entry or raw BrowserContext
    pages: list[Any] = getattr(entry, "pages", None)

    if pages and pages:
        page = pages[-1]
        await page.goto(url, wait_until="domcontentloaded")
    else:
        raw_ctx = entry.context if hasattr(entry, "context") else entry
        page = await raw_ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        if pages is not None:
            pages.append(page)

    return {"url": page.url, "title": await page.title()}


async def snapshot(
    context: Any, selector: str | None = None, compact: bool = True
) -> dict[str, Any]:
    """Capture accessibility tree snapshot."""
    page = await _get_page(context)
    if selector:
        element = await page.query_selector(selector)
        if element is None:
            raise ValueError(f"Selector not found: {selector}")
        snapshot_text = await element.inner_text()
    else:
        script = (
            "() => {"
            "  function walk(node, depth, maxDepth) {"
            "    if (!node || depth > maxDepth) return '';"
            "    let out = '';"
            "    let tag = node.tagName ? node.tagName.toLowerCase() : '';"
            "    let role = node.getAttribute('role') || '';"
            "    let label = node.getAttribute('aria-label') || '';"
            "    let id = node.id ? '#' + node.id : '';"
            "    if (tag) {"
            "      let indent = '  '.repeat(depth);"
            "      out += indent + tag + id + ' ' + role + ' ' + label + '\\n';"
            "    }"
            "    for (let c of node.children) {"
            "      out += walk(c, depth + 1, 3);"
            "    }"
            "    return out;"
            "  }"
            "  return walk(document.body, 0, 3);"
            "}"
        )
        snapshot_text = await page.evaluate(script)

    return {"snapshot": snapshot_text}


async def click(context: Any, selector: str) -> dict[str, Any]:
    """Click an element."""
    page = await _get_page(context)
    element = await page.query_selector(selector)
    if element is None:
        raise ValueError(f"Selector not found: {selector}")
    await element.scroll_into_view_if_needed()
    await element.click()
    return {"clicked": selector}


async def fill(context: Any, selector: str, value: str) -> dict[str, Any]:
    """Fill an input field."""
    page = await _get_page(context)
    await page.fill(selector, value)
    return {"filled": selector, "value": value}


async def type_text(context: Any, selector: str, text: str) -> dict[str, Any]:
    """Type text into an element."""
    page = await _get_page(context)
    element = await page.query_selector(selector)
    if element is None:
        raise ValueError(f"Selector not found: {selector}")
    await element.type(text)
    return {"typed": selector, "text": text}


async def evaluate(context: Any, script: str) -> dict[str, Any]:
    """Execute JavaScript on the page."""
    page = await _get_page(context)
    result = await page.evaluate(script)
    return {"result": result}


async def take_screenshot(
    context: Any, path: str | None = None, selector: str | None = None, full_page: bool = False
) -> dict[str, Any]:
    """Take a screenshot of the page or element."""
    page = await _get_page(context)
    kwargs: dict[str, Any] = {"full_page": full_page}
    if selector:
        element = await page.query_selector(selector)
        if element is None:
            raise ValueError(f"Selector not found: {selector}")
        data = await element.screenshot()
    else:
        data = await page.screenshot(**kwargs)

    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
        return {"path": path, "size": len(data)}

    b64 = base64.b64encode(data).decode()
    return {"base64": b64, "size": len(data)}


async def wait_for(
    context: Any,
    selector: str | None = None,
    timeout: int = 3000,
    state: str = "visible",
) -> dict[str, Any]:
    """Wait for a condition on the page."""
    page = await _get_page(context)
    if selector:
        await page.wait_for_selector(selector, state=state, timeout=timeout)
        return {"waited": selector, "state": state}
    await asyncio.sleep(timeout / 1000)
    return {"waited_ms": timeout}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_page(context: Any) -> Any:
    """Get or create the current page in the context."""
    entry = context  # ContextStore entry
    pages: list[Any] = getattr(entry, "pages", None)
    if pages and pages:
        return pages[-1]

    raw_ctx = entry.context if hasattr(entry, "context") else entry
    page = await raw_ctx.new_page()
    if pages is not None:
        pages.append(page)
    return page
