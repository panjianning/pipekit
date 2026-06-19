"""Built-in pipeline: xhs/search_to_sqlite

Search XHS and upsert results into SQLite in one command.
Demonstrates pipeline composition: xhs/search → sqlite/upsert.
"""

from __future__ import annotations

from typing import Any

meta = {
    "name": "xhs/search_to_sqlite",
    "description": "Search XHS notes by keyword and upsert into SQLite.",
    "tags": ["xiaohongshu", "search", "sqlite", "composition"],
    "input": {
        "keyword": {"required": True, "description": "Search keyword."},
        "sort": {
            "required": False,
            "default": "general",
            "description": "Sort: general / popularity_descending / time_descending.",
        },
        "num_pages": {
            "required": False,
            "default": "1",
            "description": "Number of pages (≈20/page, max 20).",
        },
        "table": {
            "required": False,
            "default": "xhs_notes",
            "description": "Target SQLite table name.",
        },
        "db_path": {
            "required": False,
            "description": "SQLite path. Default: ~/.pipekit/pipeline-data.db",
        },
    },
    "output": {
        "keyword": {"type": "str", "description": "Search keyword."},
        "total_found": {"type": "int", "description": "Notes found on XHS."},
        "written": {"type": "int", "description": "Rows written to SQLite."},
        "table": {"type": "str", "description": "Target table."},
    },
}


async def run(ctx: Any) -> dict[str, Any]:
    keyword = str(ctx.input.get("keyword", "")).strip()
    if not keyword:
        raise ValueError("xhs/search_to_sqlite requires input.keyword")

    sort = str(ctx.input.get("sort", "general")).strip()
    num_pages = max(1, min(20, int(ctx.input.get("num_pages", 1))))
    table = str(ctx.input.get("table", "xhs_notes")).strip()
    db_path = ctx.input.get("db_path")

    # Step 1: Search XHS
    ctx.log(f"Step 1: searching XHS for '{keyword}'")
    search_result = await ctx.pipeline.run("xhs/search", {
        "keyword": keyword,
        "sort": sort,
        "num_pages": num_pages,
    })

    notes = search_result.get("notes", [])
    if not notes:
        ctx.log("No notes found.")
        return {
            "keyword": keyword,
            "total_found": 0,
            "written": 0,
            "table": table,
        }

    # Step 2: Transform to flat rows for SQLite
    rows = []
    for note in notes:
        interact = note.get("interact_info", {})
        author = note.get("author", {})
        rows.append({
            "note_id": note.get("note_id", ""),
            "xsec_token": note.get("xsec_token", ""),
            "title": note.get("title", ""),
            "type": note.get("type", ""),
            "url": note.get("url", ""),
            "publish_time": note.get("publish_time", ""),
            "author_id": author.get("user_id", ""),
            "author_name": author.get("nickname", ""),
            "author_avatar": author.get("avatar", ""),
            "liked_count": interact.get("liked_count", 0),
            "collected_count": interact.get("collected_count", 0),
            "comment_count": interact.get("comment_count", 0),
            "shared_count": interact.get("shared_count", 0),
            "cover": note.get("cover", ""),
            "images": str(note.get("images", [])),
            "keyword": keyword,
        })

    # Step 3: Upsert into SQLite
    ctx.log(f"Step 2: upserting {len(rows)} rows into '{table}'")
    db_result = await ctx.pipeline.run("sqlite/upsert", {
        "table": table,
        "rows": rows,
        "unique_keys": ["note_id"],
        "update_keys": [
            "title", "type", "url", "publish_time",
            "author_id", "author_name", "author_avatar",
            "liked_count", "collected_count", "comment_count", "shared_count",
            "cover", "images",
        ],
        "db_path": db_path,
    })

    ctx.log(
        f"Done: {len(notes)} notes found, "
        f"{db_result.get('written_rows', 0)} rows written to '{table}'"
    )

    return {
        "keyword": keyword,
        "total_found": len(notes),
        "written": db_result.get("written_rows", 0),
        "table": table,
    }
