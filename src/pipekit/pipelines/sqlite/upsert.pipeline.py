"""Built-in pipeline: sqlite/upsert

Create table if needed and batch upsert rows into SQLite.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

meta = {
    "name": "sqlite/upsert",
    "description": "Create table if needed and batch upsert rows into SQLite.",
    "tags": ["storage", "database", "sqlite"],
    "input": {
        "table": {"required": True, "description": "Target table name."},
        "rows": {"required": False, "description": "List of dict rows to upsert."},
        "db_path": {
            "required": False,
            "description": "SQLite path. Default: ~/.pipekit/pipeline-data.db",
        },
        "unique_keys": {
            "required": False,
            "description": "Columns for the unique conflict target.",
        },
        "update_keys": {
            "required": False,
            "description": "Columns to update on conflict (empty = DO NOTHING).",
        },
        "column_types": {
            "required": False,
            "description": "Optional type overrides, e.g. {\"count\": \"INTEGER\"}.",
        },
    },
    "output": {
        "ok": {"type": "bool", "description": "Whether the operation succeeded."},
        "db_path": {"type": "str", "description": "Resolved SQLite database path."},
        "table": {"type": "str", "description": "Target table name."},
        "total_rows": {"type": "int", "description": "Input row count."},
        "written_rows": {"type": "int", "description": "Rows actually inserted/updated."},
        "unique_keys": {"type": "list", "description": "Conflict target columns."},
        "update_keys": {"type": "list", "description": "Columns updated on conflict."},
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_TYPES = frozenset({"NULL", "INTEGER", "REAL", "TEXT", "BLOB", "NUMERIC"})
_HOME = Path.home() / ".pipekit"


def _quote(name: str) -> str:
    n = str(name).strip()
    if not _IDENT_RE.match(n):
        raise ValueError(f"Invalid SQL identifier: {n}")
    return f'"{n}"'


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def _normalize_type(raw: Any) -> str:
    t = str(raw).strip().upper()
    if t not in _ALLOWED_TYPES:
        raise ValueError(f"Unsupported SQLite type: {raw}")
    return t


def _to_sql_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _resolve_db_path(input_db_path: str | None) -> Path:
    if input_db_path and input_db_path.strip():
        return Path(input_db_path.strip()).expanduser().resolve()
    return _HOME / "pipeline-data.db"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(ctx: Any) -> dict[str, Any]:
    table = str(ctx.input.get("table", "")).strip()
    if not table:
        raise ValueError("sqlite/upsert requires input.table")

    rows_input = ctx.input.get("rows") or []
    if not isinstance(rows_input, list):
        raise ValueError("sqlite/upsert input.rows must be a list")
    rows = [r for r in rows_input if isinstance(r, dict)]

    db_path = _resolve_db_path(ctx.input.get("db_path"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    unique_keys = [str(x) for x in (ctx.input.get("unique_keys") or []) if str(x).strip()]
    update_keys = [str(x) for x in (ctx.input.get("update_keys") or []) if str(x).strip()]
    column_types_input = ctx.input.get("column_types") or {}

    if not rows:
        return {
            "ok": True,
            "db_path": str(db_path),
            "table": table,
            "total_rows": 0,
            "written_rows": 0,
            "unique_keys": unique_keys,
            "update_keys": update_keys,
        }

    # Collect columns
    columns = sorted({k for row in rows for k in row})
    if not columns:
        raise ValueError("sqlite/upsert: no columns found in rows")

    # Infer types
    types: dict[str, str] = dict.fromkeys(columns, "TEXT")
    for row in rows:
        for col, val in row.items():
            types[col] = _infer_type(val)
    for col, raw in column_types_input.items():
        types[str(col)] = _normalize_type(raw)

    col_defs = ", ".join(f"{_quote(c)} {types[c]}" for c in columns)

    conn = sqlite3.connect(str(db_path))
    try:
        # Create table
        conn.execute(f"CREATE TABLE IF NOT EXISTS {_quote(table)} ({col_defs})")

        # Unique index
        if unique_keys:
            idx_name = f"idx_{table}_{'_'.join(unique_keys)}_uniq"
            idx_cols = ", ".join(_quote(c) for c in unique_keys)
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {_quote(idx_name)} "
                f"ON {_quote(table)} ({idx_cols})"
            )

        # Build upsert SQL
        insert_cols = ", ".join(_quote(c) for c in columns)
        placeholders = ", ".join("?" for _ in columns)

        if unique_keys:
            conflict_cols = ", ".join(_quote(c) for c in unique_keys)
            if update_keys:
                set_clause = ", ".join(
                    f"{_quote(c)}=excluded.{_quote(c)}" for c in update_keys
                )
                sql = (
                    f"INSERT INTO {_quote(table)} ({insert_cols}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {set_clause}"
                )
            else:
                sql = (
                    f"INSERT INTO {_quote(table)} ({insert_cols}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({conflict_cols}) DO NOTHING"
                )
        else:
            sql = f"INSERT INTO {_quote(table)} ({insert_cols}) VALUES ({placeholders})"

        written = 0
        for row in rows:
            values = [_to_sql_value(row.get(c)) for c in columns]
            cursor = conn.execute(sql, values)
            written += cursor.rowcount

        conn.commit()
        ctx.log(f"sqlite/upsert: wrote {written} rows to table '{table}'")

        return {
            "ok": True,
            "db_path": str(db_path),
            "table": table,
            "total_rows": len(rows),
            "written_rows": written,
            "unique_keys": unique_keys,
            "update_keys": update_keys,
        }
    finally:
        conn.close()
