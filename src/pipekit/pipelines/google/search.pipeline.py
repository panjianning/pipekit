"""Built-in pipeline: google/search

Search Google via simulated input + DOM parsing.
Tests anti-detection (no Playwright managed launch, no --enable-automation).
"""

from __future__ import annotations

import json
from typing import Any

meta = {
    "name": "google/search",
    "description": "Search Google via input simulation + DOM result extraction.",
    "tags": ["google", "search", "web"],
    "input": {
        "query": {"required": True, "description": "Search query."},
        "pages": {
            "required": False,
            "default": "1",
            "description": "Number of result pages (≈10/page, max 5).",
        },
    },
    "output": {
        "query": {"type": "str", "description": "Search query."},
        "count": {"type": "int", "description": "Total results."},
        "pages_fetched": {"type": "int", "description": "Pages actually fetched."},
        "results": {"type": "array", "description": "Result list [{title, url, snippet}]."},
    },
}

# ---------------------------------------------------------------------------
# JS scripts
# ---------------------------------------------------------------------------


def _input_script(query: str) -> str:
    """Fill the Google search box and submit."""
    return f"""
(() => {{
    var q = {json.dumps(query)};
    // Try multiple selectors — Google changes them occasionally
    var input = document.querySelector('textarea[name="q"]')
             || document.querySelector('input[name="q"]')
             || document.querySelector('[aria-label="Search"]')
             || document.querySelector('input[type="text"]');
    if (!input) return JSON.stringify({{error: 'Search input not found'}});

    var nativeSetter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype, 'value'
    ) || Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, 'value'
    );
    if (nativeSetter && nativeSetter.set) {{
        nativeSetter.set.call(input, q);
    }} else {{
        input.value = q;
    }}
    input.dispatchEvent(new Event('input', {{bubbles: true}}));

    // Submit via form or Enter key
    var form = input.closest('form');
    if (form) {{
        form.submit();
    }} else {{
        input.focus();
        var ev = {{key:'Enter', code:'Enter', keyCode:13, which:13,
                   bubbles:true, cancelable:true}};
        for (var k of ['keydown','keypress','keyup']) {{
            input.dispatchEvent(new KeyboardEvent(k, ev));
        }}
    }}
    return JSON.stringify({{ok: true}});
}})()
"""


def _parse_script(pages: int, query: str) -> str:
    """Fetch subsequent pages via fetch() + DOMParser, parse all results."""
    return f"""
(async () => {{
    var seen = new Set();
    var allResults = [];
    var pagesFetched = 0;
    var q = {json.dumps(query)};

    function parseResults(html) {{
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var h3s = doc.querySelectorAll('h3');
        var pageResults = [];
        for (var i = 0; i < h3s.length; i++) {{
            var h3 = h3s[i];
            var a = h3.closest('a');
            if (!a) continue;
            var link = a.getAttribute('href');
            if (!link || !link.startsWith('http')) continue;
            var title = h3.textContent.trim();
            if (seen.has(link)) continue;
            seen.add(link);

            var snippet = '';
            var container = a;
            while (container.parentElement && container.parentElement.tagName !== 'BODY') {{
                var sibs = Array.from(container.parentElement.children);
                if (sibs.filter(function(s) {{ return s.querySelector('h3'); }}).length > 1) break;
                container = container.parentElement;
            }}
            var spans = container.querySelectorAll('span');
            for (var j = 0; j < spans.length; j++) {{
                var t = spans[j].textContent.trim();
                if (t.length > 30 && t !== title) {{ snippet = t; break; }}
            }}
            pageResults.push({{ title: title, url: link, snippet: snippet }});
        }}
        return pageResults;
    }}

    // First page: parse the current page (already loaded by form submit)
    var firstPage = parseResults(document.documentElement.innerHTML);
    allResults.push.apply(allResults, firstPage);
    pagesFetched = 1;

    // Subsequent pages via fetch
    for (var p = 1; p < {pages}; p++) {{
        var start = p * 10;
        var url = 'https://www.google.com/search?q='
            + encodeURIComponent(q) + '&start=' + start;
        try {{
            var resp = await fetch(url, {{ credentials: 'include' }});
            if (!resp.ok) break;
            var html = await resp.text();
            var pageResults = parseResults(html);
            if (pageResults.length === 0) break;
            allResults.push.apply(allResults, pageResults);
            pagesFetched++;
        }} catch(e) {{
            break;
        }}
    }}

    return JSON.stringify({{
        query: q,
        count: allResults.length,
        pages_fetched: pagesFetched,
        results: allResults,
    }});
}})()
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(ctx: Any) -> dict[str, Any]:
    query = str(ctx.input.get("query", "")).strip()
    if not query:
        raise ValueError("google/search requires input.query")

    pages = max(1, min(5, int(ctx.input.get("pages", 1))))

    ctx.log(f"google/search: query={query}, pages={pages}")

    # 1. Navigate to Google
    page = await ctx.browser.navigate("https://www.google.com")
    await page.wait_for_timeout(3000)

    # 2. Check if Google blocked us (anti-detection test)
    blocked = await page.evaluate(
        "() => document.title.includes('Sorry')"
        " || document.body.innerText.includes('unusual traffic')"
        " || document.body.innerText.includes('automated requests')"
    )
    if blocked:
        raise RuntimeError(
            "Google blocked the request (bot detection). "
            "Try again or check Chrome profile."
        )

    # 3. Fill search box + submit
    enter_raw = await page.evaluate(_input_script(query))
    enter_result = json.loads(enter_raw)
    if enter_result.get("error"):
        raise RuntimeError(f"google/search input failed: {enter_result['error']}")

    # 4. Wait for results page
    await page.wait_for_timeout(2000)

    # 5. Parse results (current page + fetch more)
    raw = await page.evaluate(_parse_script(pages, query))

    data: dict[str, Any]
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"Failed to parse results: {str(raw)[:200]}") from exc

    if data.get("error"):
        raise RuntimeError(f"google/search failed: {data['error']}")

    ctx.log(
        f"google/search: {data.get('count', 0)} results "
        f"across {data.get('pages_fetched', 0)} pages"
    )

    return data
