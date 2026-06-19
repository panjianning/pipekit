"""Built-in pipeline: mongo/upsert

Batch upsert rows into a MongoDB collection.
"""

from __future__ import annotations

from typing import Any

meta = {
    "name": "mongo/upsert",
    "description": "Batch upsert rows into a MongoDB collection.",
    "tags": ["storage", "database", "mongodb"],
    "input": {
        "uri": {"required": False, "description": "MongoDB connection URI. Default: mongodb://localhost:27017"},
        "database": {"required": True, "description": "Database name."},
        "collection": {"required": True, "description": "Collection name."},
        "rows": {"required": False, "description": "List of dict rows to upsert."},
        "unique_keys": {"required": True, "description": "Fields for the upsert filter."},
        "update_keys": {
            "required": False,
            "description": "Fields to update on match (default: all non-key fields).",
        },
    },
    "output": {
        "ok": {"type": "bool", "description": "Whether the operation succeeded."},
        "database": {"type": "str", "description": "Database name."},
        "collection": {"type": "str", "description": "Collection name."},
        "total_rows": {"type": "int", "description": "Input row count."},
        "matched": {"type": "int", "description": "Documents matched."},
        "modified": {"type": "int", "description": "Documents modified."},
        "upserted": {"type": "int", "description": "Documents inserted."},
    },
}


async def run(ctx: Any) -> dict[str, Any]:
    try:
        from pymongo import MongoClient, UpdateOne
    except ImportError as exc:
        raise RuntimeError("pymongo is not installed. Run: pip install pymongo") from exc

    uri = str(ctx.input.get("uri") or "mongodb://localhost:27017")
    database = str(ctx.input.get("database", "")).strip()
    collection = str(ctx.input.get("collection", "")).strip()
    rows = ctx.input.get("rows") or []
    unique_keys = [str(x) for x in (ctx.input.get("unique_keys") or []) if str(x).strip()]
    update_keys = [str(x) for x in (ctx.input.get("update_keys") or []) if str(x).strip()]

    if not database:
        raise ValueError("mongo/upsert requires input.database")
    if not collection:
        raise ValueError("mongo/upsert requires input.collection")
    if not unique_keys:
        raise ValueError("mongo/upsert requires input.unique_keys")
    if not isinstance(rows, list):
        raise ValueError("mongo/upsert input.rows must be a list")

    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return {
            "ok": True,
            "database": database,
            "collection": collection,
            "total_rows": 0,
            "matched": 0,
            "modified": 0,
            "upserted": 0,
        }

    ops = []
    for row in rows:
        filter_doc = {k: row.get(k) for k in unique_keys}
        if any(v is None for v in filter_doc.values()):
            continue
        keys = update_keys or [k for k in row if k not in unique_keys]
        set_doc = {k: row.get(k) for k in keys}
        ops.append(
            UpdateOne(filter_doc, {"$set": set_doc, "$setOnInsert": filter_doc}, upsert=True)
        )

    client = MongoClient(uri)
    try:
        ctx.log(f"mongo/upsert: connecting to {database}.{collection}")
        col = client[database][collection]
        result = col.bulk_write(ops, ordered=False) if ops else None
        ctx.log(
            f"mongo/upsert: matched={result.matched_count if result else 0}, "
            f"modified={result.modified_count if result else 0}, "
            f"upserted={len(result.upserted_ids) if result else 0}"
        )
        return {
            "ok": True,
            "database": database,
            "collection": collection,
            "total_rows": len(rows),
            "matched": int(result.matched_count if result else 0),
            "modified": int(result.modified_count if result else 0),
            "upserted": int(len(result.upserted_ids) if result else 0),
        }
    finally:
        client.close()
