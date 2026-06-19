"""Built-in pipeline: xhs/note

Fetch XHS (小红书) note details — title, body, author, interact stats.
Supports both full URLs and short links (xhslink.com).
"""

from __future__ import annotations

import json
from typing import Any

meta = {
    "name": "xhs/note",
    "description": (
        "Fetch XHS note details: title, body, author, interact stats."
        " Supports short links."
    ),
    "tags": ["xiaohongshu", "note", "detail", "social-media"],
    "input": {
        "url": {
            "required": True,
            "description": "Note URL (full or short link like xhslink.com/...).",
        },
    },
    "output": {
        "note_id": {"type": "str", "description": "Note ID."},
        "xsec_token": {"type": "str", "description": "Security token from URL."},
        "title": {"type": "str", "description": "Note title."},
        "desc": {"type": "str", "description": "Note body text."},
        "type": {"type": "str", "description": "Note type (normal / video)."},
        "url": {"type": "str", "description": "Canonical note URL."},
        "author": {"type": "str", "description": "Author nickname."},
        "author_id": {"type": "str", "description": "Author user ID."},
        "likes": {"type": "str", "description": "Like count."},
        "comments": {"type": "str", "description": "Comment count."},
        "collects": {"type": "str", "description": "Collect count."},
        "shares": {"type": "str", "description": "Share count."},
        "tags": {"type": "array", "description": "Hashtag list."},
        "images": {"type": "array", "description": "Image URLs."},
        "created_time": {"type": "int", "description": "Publish timestamp (ms)."},
    },
}

# ---------------------------------------------------------------------------
# JS extraction script (uses new RegExp to avoid escape hell)
# ---------------------------------------------------------------------------

_EXTRACT_SCRIPT = """
(() => {
    // Extract note_id and xsec_token from URL
    var m = window.location.pathname.match(
        new RegExp('/(?:discovery/item|explore)/([^/?#]+)')
    );
    var noteId = m ? m[1] : '';
    var xsecToken = new URL(window.location.href).searchParams.get('xsec_token') || '';

    if (!noteId || !xsecToken) {
        return JSON.stringify({ error: 'Missing note_id or xsec_token in URL' });
    }

    // Check for captcha
    var bodyText = document.body.innerText || '';
    if (bodyText.indexOf('verify.xiaohongshu.com') !== -1
        || bodyText.indexOf('captcha') !== -1) {
        return JSON.stringify({
            error: 'Captcha required',
            hint: 'Please solve captcha manually'
        });
    }

    // Extract __INITIAL_STATE__
    var html = document.documentElement.innerHTML;
    var re = new RegExp('window\\\\.__INITIAL_STATE__=(.+?)<\\\\/script>');
    var match = html.match(re);
    if (!match) {
        return JSON.stringify({
            error: 'Initial state not found — page may be restricted'
        });
    }

    var state;
    try {
        state = new Function('return ' + match[1])();
    } catch (e) {
        return JSON.stringify({ error: 'Failed to parse initial state: ' + e.message });
    }

    var noteMap = state && state.note && state.note.noteDetailMap ? state.note.noteDetailMap : {};
    var note = noteMap[noteId] && noteMap[noteId].note;

    // Fallback: take first valid note
    if (!note) {
        var keys = Object.keys(noteMap).filter(function(k) { return k !== 'undefined'; });
        if (keys.length > 0) note = noteMap[keys[0]] && noteMap[keys[0]].note;
    }

    if (!note) {
        return JSON.stringify({
            error: 'Note not found — may be private or deleted'
        });
    }

    var user = note.user || {};
    var interact = note.interactInfo || {};
    var images = (note.imageList || []).map(function(img) {
        var info = (img.infoList || []).filter(function(i) {
            return i.imageScene === 'WB_DFT';
        })[0];
        return info ? info.url : (img.urlDefault || img.urlPre || '');
    }).filter(Boolean);

    return JSON.stringify({
        note_id: note.noteId || noteId,
        xsec_token: xsecToken,
        title: note.title || '',
        desc: note.desc || '',
        type: note.type || '',
        url: 'https://www.xiaohongshu.com/explore/'
            + (note.noteId || noteId)
            + '?xsec_token=' + xsecToken,
        author: user.nickname || '',
        author_id: user.userId || '',
        likes: interact.likedCount || '',
        comments: interact.commentCount || '',
        collects: interact.collectedCount || '',
        shares: interact.shareCount || '',
        tags: (note.tagList || []).map(function(t) { return t.name || ''; }).filter(Boolean),
        images: images,
        created_time: note.time || 0,
    });
})()
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(ctx: Any) -> dict[str, Any]:
    url = str(ctx.input.get("url", "")).strip()
    if not url:
        raise ValueError("xhs/note requires input.url")

    ctx.log(f"xhs/note: fetching {url}")

    # 1. Navigate to XHS home to establish session
    page = await ctx.browser.navigate("https://www.xiaohongshu.com")
    await page.wait_for_timeout(3000)

    # Check login (XHS uses a1 cookie)
    a1_cookie = await page.evaluate(
        "() => document.cookie.includes('a1')"
    )
    if not a1_cookie:
        raise RuntimeError(
            "Not logged in to XHS. "
            "Open the Chrome window, log in at https://www.xiaohongshu.com, "
            "then retry."
        )

    # 2. Navigate to the note URL (supports short links via redirect)
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)

    # 3. Extract note data from __INITIAL_STATE__
    raw = await page.evaluate(_EXTRACT_SCRIPT)

    data: dict[str, Any]
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"Failed to parse note data: {str(raw)[:200]}"
        ) from exc

    if data.get("error"):
        raise RuntimeError(f"xhs/note failed: {data['error']}")

    ctx.log(f"xhs/note: fetched '{data.get('title', '')[:40]}'")

    return data
