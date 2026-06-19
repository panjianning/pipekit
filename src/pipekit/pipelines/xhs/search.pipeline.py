"""Built-in pipeline: xhs/search

Search XHS (小红书) notes by keyword using input simulation + XHR interception.
Handles both classic search (#search-input) and AI search (textarea).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

meta = {
    "name": "xhs/search",
    "description": (
        "Search XHS notes by keyword"
        " (input simulation + XHR interception + scroll pagination)."
    ),
    "tags": ["xiaohongshu", "search", "social-media"],
    "input": {
        "keyword": {"required": True, "description": "Search keyword."},
        "sort": {
            "required": False,
            "default": "general",
            "description": (
                "Sort: general / popularity_descending / time_descending"
                " / comment_descending / collect_descending"
            ),
        },
        "num_pages": {
            "required": False,
            "default": "1",
            "description": "Pages to fetch (≈20/page, max 20).",
        },
    },
    "output": {
        "keyword": {"type": "str", "description": "Search keyword."},
        "sort": {"type": "str", "description": "Sort order used."},
        "pages_fetched": {"type": "int", "description": "Pages fetched."},
        "total_count": {"type": "int", "description": "Total notes collected."},
        "has_more": {"type": "bool", "description": "More pages available."},
        "notes": {"type": "array", "description": "Note list."},
    },
}


def _xhr_script(keyword: str, sort: str, num_pages: int) -> str:
    """JS script that intercepts search/notes XHR after the page navigates."""
    return f"""
(async () => {{
    const TARGET_SORT = {json.dumps(sort)};

    // Accumulate search/notes responses
    const pageResponses = [];
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {{
        this.__isSearch = typeof url === 'string' && url.includes('search/notes');
        return origOpen.apply(this, arguments);
    }};

    XMLHttpRequest.prototype.send = function(body) {{
        if (this.__isSearch && typeof body === 'string') {{
            try {{
                let data = JSON.parse(body);
                data.sort = TARGET_SORT;
                if (Array.isArray(data.filters)) {{
                    let sf = data.filters.find(f => f.type === 'sort_type');
                    if (sf) {{ sf.tags = [TARGET_SORT]; }}
                    else {{ data.filters.push({{tags: [TARGET_SORT], type: 'sort_type'}}); }}
                }}
                body = JSON.stringify(data);
            }} catch(_) {{}}

            const x = this;
            const prev = x.onreadystatechange;
            x.onreadystatechange = function() {{
                if (x.readyState === 4) {{
                    try {{
                        const r = JSON.parse(x.responseText);
                        if (r.success) pageResponses.push(r);
                    }} catch(_) {{}}
                }}
                if (prev) prev.apply(this, arguments);
            }};
        }}
        return origSend.apply(this, [body]);
    }};

    // Wait for first response (triggered by page navigation after input+Enter)
    let attempts = 0;
    while (pageResponses.length === 0 && attempts < 80) {{
        await new Promise(r => setTimeout(r, 200));
        attempts++;
    }}

    if (pageResponses.length === 0) {{
        XMLHttpRequest.prototype.open = origOpen;
        XMLHttpRequest.prototype.send = origSend;
        return JSON.stringify({{error: 'No search XHR captured — page may not have navigated to search results'}});
    }}

    // Scroll for more pages
    for (let page = 1; page < {num_pages}; page++) {{
        const last = pageResponses[pageResponses.length - 1];
        if (!last?.data?.has_more) break;
        window.scrollTo(0, document.body.scrollHeight);
        const prevLen = pageResponses.length;
        for (let i = 0; i < 80; i++) {{
            if (pageResponses.length > prevLen) break;
            window.scrollBy(0, 300);
            await new Promise(r => setTimeout(r, 200));
        }}
    }}

    XMLHttpRequest.prototype.open = origOpen;
    XMLHttpRequest.prototype.send = origSend;

    // Merge all pages
    const allNotes = [];
    let lastHasMore = false;
    for (const resp of pageResponses) {{
        const items = resp.data?.items || [];
        for (const it of items) {{
            const card = it.note_card || {{}};
            const user = card.user || {{}};
            const interact = card.interact_info || {{}};
            const note = {{
                note_id: it.id || card.note_id,
                xsec_token: it.xsec_token || '',
                title: card.display_title || '',
                type: card.type || '',
                url: ('https://www.xiaohongshu.com/explore/'
                    + (it.id || card.note_id)
                    + '?xsec_token=' + (it.xsec_token || '')),
                publish_time: (
                    card.corner_tag_info?.[0]
                    ? card.corner_tag_info[0].text : ''
                ),
                author: {{
                    user_id: user.user_id || '',
                    nickname: user.nickname || '',
                    avatar: user.avatar || '',
                }},
                interact_info: {{
                    liked_count: interact.liked_count || 0,
                    collected_count: interact.collected_count || 0,
                    comment_count: interact.comment_count || 0,
                    shared_count: interact.shared_count || 0,
                }},
                cover: card.cover?.url_default || '',
                images: Array.isArray(card.image_list)
                    ? card.image_list.map(img => {{
                        const info = (img.info_list || [])
                            .find(i => i.image_scene === 'WB_DFT');
                        return info ? info.url : '';
                    }}).filter(Boolean)
                    : [],
            }};
            if (note.type) allNotes.push(note);
        }}
        lastHasMore = resp.data?.has_more || false;
    }}

    return JSON.stringify({{
        pages_fetched: pageResponses.length,
        total_count: allNotes.length,
        has_more: lastHasMore,
        notes: allNotes,
    }});
}})()
"""


def _input_enter_script(keyword: str) -> str:
    """Find the active search input, fill it, and press Enter."""
    return f"""
(() => {{
    const ta = document.querySelector("textarea[name='aiSearchTextarea']")
            || document.querySelector('#search-input');
    if (!ta) return JSON.stringify({{
        error: 'Search input not found — page structure may have changed'
    }});

    const kw = {json.dumps(keyword)};
    const nativeSetter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype, 'value'
    ) || Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, 'value'
    );

    if (nativeSetter && nativeSetter.set) {{
        nativeSetter.set.call(ta, kw);
    }} else {{
        ta.value = kw;
    }}
    ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
    ta.dispatchEvent(new Event('change', {{ bubbles: true }}));

    ta.focus();
    const ev = {{key:'Enter', code:'Enter', keyCode:13, which:13,
                bubbles:true, cancelable:true}};
    for (const k of ['keydown','keypress','keyup']) {{
        ta.dispatchEvent(new KeyboardEvent(k, ev));
    }}

    return JSON.stringify({{ok: true, input_type: ta.tagName}});
}})()
"""


async def run(ctx: Any) -> dict[str, Any]:
    keyword = str(ctx.input.get("keyword", "")).strip()
    if not keyword:
        raise ValueError("xhs/search requires input.keyword")

    sort = str(ctx.input.get("sort", "general")).strip()
    num_pages = max(1, min(20, int(ctx.input.get("num_pages", 1))))

    ctx.log(f"xhs/search: keyword={keyword}, sort={sort}, pages={num_pages}")

    # 1. Navigate to XHS
    page = await ctx.browser.navigate("https://www.xiaohongshu.com")
    await page.wait_for_timeout(3000)

    # 2. Check login (XHS uses a1 cookie for identity)
    a1_cookie = await page.evaluate(
        "() => document.cookie.includes('a1')"
    )
    if not a1_cookie:
        raise RuntimeError(
            "Not logged in to XHS. "
            "Open the Chrome window, log in at https://www.xiaohongshu.com, "
            "then retry."
        )

    # 3. Inject XHR interception first, then trigger search
    #    XHR listener starts listening, then Enter triggers the page nav
    xhr_task = asyncio.ensure_future(page.evaluate(_xhr_script(keyword, sort, num_pages)))
    await asyncio.sleep(0.5)  # let the interceptor hook in

    # 4. Fill input + press Enter (triggers page nav to search results)
    enter_raw = await page.evaluate(_input_enter_script(keyword))
    enter_result = json.loads(enter_raw)
    if enter_result.get("error"):
        raise RuntimeError(f"xhs/search input failed: {enter_result['error']}")
    ctx.log(f"search input: type={enter_result.get('input_type', 'unknown')}")

    # 5. Wait for XHR interception to finish collecting results
    raw = await xhr_task

    data: dict[str, Any]
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"Failed to parse search result: {str(raw)[:200]}"
        ) from exc

    if data.get("error"):
        raise RuntimeError(f"xhs/search failed: {data['error']}")

    ctx.log(
        f"xhs/search: fetched {data.get('pages_fetched', 0)} pages, "
        f"{data.get('total_count', 0)} notes"
    )

    return {
        "keyword": keyword,
        "sort": sort,
        **data,
    }
