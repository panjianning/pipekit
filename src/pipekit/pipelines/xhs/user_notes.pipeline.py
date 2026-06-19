"""Built-in pipeline: xhs/user_notes

Get XHS (小红书) user's published notes by navigating to profile,
intercepting user_posted XHR responses, and scrolling for pagination.
"""

from __future__ import annotations

import json
from typing import Any

meta = {
    "name": "xhs/user_notes",
    "description": (
        "Get XHS user's notes list"
        " (navigate to profile + XHR interception + scroll pagination)."
    ),
    "tags": ["xiaohongshu", "user", "notes", "social-media"],
    "input": {
        "user_id": {"required": True, "description": "XHS user ID (short or long)."},
        "num_pages": {
            "required": False,
            "default": "1",
            "description": "Pages to fetch (≈30 notes/page, max 20).",
        },
    },
    "output": {
        "user_id": {"type": "str", "description": "User ID."},
        "pages_fetched": {"type": "int", "description": "Pages fetched."},
        "total_count": {"type": "int", "description": "Total notes collected."},
        "has_more": {"type": "bool", "description": "More pages available."},
        "next_cursor": {"type": "str", "description": "Cursor for next page."},
        "page_cursors": {"type": "array", "description": "Cursors per page."},
        "notes": {
            "type": "array",
            "description": "Note list: note_id, title, url, liked_count.",
        },
    },
}


def _adapter_script(user_id: str, num_pages: int) -> str:
    """JS script that intercepts user_posted XHR on the profile page.

    Strategy:
     1. Extract real user ID from URL path (short id → long id)
     2. Wait for Vue router, then hook XHR interception
     3. Push /explore then back to profile via Vue Router → triggers user_posted XHR
     4. The app constructs properly signed requests; we just capture responses
     5. Scroll to bottom to trigger infinite-scroll pagination
    """
    return f"""
(async () => {{
    const args = {{ user_id: {json.dumps(user_id)}, num_pages: {num_pages} }};

    // Extract real user_id from URL (short id is XHS handle, API uses long id)
    const pathSegments = window.location.pathname.split('/');
    const profileUserId = pathSegments[pathSegments.length - 1] || args.user_id;

    // Wait for Vue router to be available
    let app = document.querySelector('#app')?.__vue_app__;
    let router = app?.config?.globalProperties?.$router;
    let waitCnt = 1;
    while (!router && waitCnt <= 15) {{
        await new Promise(r => setTimeout(r, 300));
        app = document.querySelector('#app')?.__vue_app__;
        router = app?.config?.globalProperties?.$router;
        waitCnt += 1;
    }}
    if (!router) {{
        return JSON.stringify({{
            error: 'Vue router not found',
            hint: 'Please ensure you are logged in and on a XHS page'
        }});
    }}

    // Intercept user_posted XHR responses
    const pageResponses = [];
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url) {{
        this.__isTarget = typeof url === 'string'
            && url.includes('user_posted')
            && url.includes(profileUserId);
        return origOpen.apply(this, arguments);
    }};

    XMLHttpRequest.prototype.send = function(body) {{
        if (this.__isTarget) {{
            const x = this;
            const prev = x.onreadystatechange;
            x.onreadystatechange = function() {{
                if (x.readyState === 4) {{
                    try {{
                        const resp = JSON.parse(x.responseText);
                        if (resp.success) pageResponses.push(resp);
                    }} catch(_) {{}}
                }}
                if (prev) prev.apply(this, arguments);
            }};
        }}
        return origSend.apply(this, arguments);
    }};

    const profilePath = window.location.pathname + window.location.search;

    try {{
        // Navigate away then back via Vue Router to trigger client-side data fetch.
        // (A direct page.goto() gives us SSR data, not the XHR we want to intercept.)
        await router.push('/explore');
        await new Promise(r => setTimeout(r, 500));
        await router.push(profilePath);

        // Wait for page 1 response
        for (let i = 0; i < 100; i++) {{
            if (pageResponses.length >= 1) break;
            await new Promise(r => setTimeout(r, 100));
        }}

        if (pageResponses.length === 0) {{
            return JSON.stringify({{
                error: 'Fetch timeout — page 1 not loaded',
                profilePath: profilePath,
                profileUserId: profileUserId
            }});
        }}

        // Scroll to load additional pages
        for (let page = 1; page < args.num_pages; page++) {{
            const lastResp = pageResponses[pageResponses.length - 1];
            if (!lastResp?.data?.has_more) break;
            window.scrollTo(0, document.body.scrollHeight);
            const prevLen = pageResponses.length;
            for (let i = 0; i < 60; i++) {{
                if (pageResponses.length > prevLen) break;
                window.scrollBy(0, 300);
                await new Promise(r => setTimeout(r, 200));
            }}
        }}
    }} catch (err) {{
        return JSON.stringify({{ error: 'Execution error: ' + err.message }});
    }} finally {{
        XMLHttpRequest.prototype.open = origOpen;
        XMLHttpRequest.prototype.send = origSend;
    }}

    // Combine all notes from all pages
    const allNotes = [];
    const pageCursors = [];
    let lastHasMore = false;

    for (const resp of pageResponses) {{
        const notes = (resp.data?.notes || []).map(n => ({{
            note_id: n.note_id || n.id,
            xsec_token: n.xsec_token,
            title: n.display_title,
            type: n.type,
            url: 'https://www.xiaohongshu.com/explore/'
                + (n.note_id || n.id)
                + '?xsec_token=' + (n.xsec_token || ''),
            liked_count: n.interact_info?.liked_count,
        }}));
        allNotes.push(...notes);
        pageCursors.push(resp.data?.cursor || '');
        lastHasMore = resp.data?.has_more || false;
    }}

    return JSON.stringify({{
        user_id: args.user_id,
        pages_fetched: pageResponses.length,
        total_count: allNotes.length,
        has_more: lastHasMore,
        next_cursor: pageCursors[pageCursors.length - 1] || '',
        page_cursors: pageCursors,
        notes: allNotes
    }});
}})()
"""


async def run(ctx: Any) -> dict[str, Any]:
    user_id = str(ctx.input.get("user_id", "")).strip()
    if not user_id:
        raise ValueError("xhs/user_notes requires input.user_id")

    num_pages = max(1, min(20, int(ctx.input.get("num_pages", 1))))

    ctx.log(f"xhs/user_notes: user_id={user_id}, num_pages={num_pages}")

    # 1. Navigate to XHS home to establish session
    page = await ctx.browser.navigate("https://www.xiaohongshu.com")
    await page.wait_for_timeout(3000)

    # 2. Check login
    logged_in = await page.evaluate(
        """() => {
            if (document.querySelector('.side-bar-ai-un-loggedIn')) return false;
            var body = document.body ? document.body.innerText || '' : '';
            if (body.indexOf('手机号登录') !== -1 && body.indexOf('获取验证码') !== -1) return false;
            return true;
        }"""
    )
    if not logged_in:
        raise RuntimeError(
            "Not logged in to XHS. "
            "Open the Chrome window, log in at https://www.xiaohongshu.com, "
            "then retry."
        )

    # 3. Navigate to user profile
    profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
    ctx.log(f"navigating to profile: {profile_url}")
    await page.goto(profile_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # 4. Run adapter script: hook XHR → Vue Router dance → scroll → collect
    raw = await page.evaluate(_adapter_script(user_id, num_pages))

    data: dict[str, Any]
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"Failed to parse user_notes result: {str(raw)[:200]}"
        ) from exc

    if data.get("error"):
        raise RuntimeError(f"xhs/user_notes failed: {data['error']}")

    ctx.log(
        f"xhs/user_notes: fetched {data.get('pages_fetched', 0)} pages, "
        f"{data.get('total_count', 0)} notes"
    )

    return data
