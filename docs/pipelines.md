# Pipeline Reference

## Pipeline File Format

Every pipeline is a Python file ending in `.pipeline.py` with two required elements:

```python
# 1. Metadata (required)
meta = {
    "name": "namespace/name",     # Required: globally unique identifier
    "description": "...",         # Optional
    "tags": ["crawler", "db"],    # Optional
    "input": { ... },             # Optional: parameter definitions
    "output": { ... },            # Optional: return value schema
}

# 2. Entry function (required)
async def run(ctx):
    """Must be async. Must return a plain dict."""
    return {"ok": True}
```

## ctx API Reference

### ctx.input

The merged input dictionary. Defaults from `meta.input` are applied automatically.

```python
async def run(ctx):
    keyword = ctx.input["keyword"]          # Required params guaranteed to exist
    limit = int(ctx.input.get("limit", 20)) # Optional with defaults
```

### ctx.log(message)

Append a timestamped line to the run log.

```python
ctx.log(f"Processing {count} items")
ctx.log(f"Item {i} failed: {error}")
```

### ctx.work_dir

Absolute path to this run's isolated working directory. Use this for any file I/O that should be scoped to the current run.

```python
import json
from pathlib import Path

output = ctx.work_dir / "output.json"
output.write_text(json.dumps(data))
```

### ctx.browser

Browser automation via Playwright. Opens pages in the run's isolated context.

#### ctx.browser.navigate(url)

Open a new page and navigate to the URL. Returns a Playwright `Page` object.

```python
page = await ctx.browser.navigate("https://example.com")
title = await page.title()
```

#### ctx.browser.evaluate(script)

Execute JavaScript in a fresh page (page is auto-closed after). Returns the script's return value.

```python
result = await ctx.browser.evaluate("""
    document.querySelector('.price').textContent
""")
```

#### ctx.new_page()

For advanced use: create a raw Playwright Page in the isolated context. You're responsible for closing it.

```python
page = await ctx.new_page()
await page.goto("https://example.com")
# ... interact ...
await page.close()
```

### ctx.pipeline

Call other pipelines by name.

#### ctx.pipeline.run(name, input=None)

Execute a sub-pipeline. The sub-pipeline runs in the same browser context. Input is merged: `ctx.input` from the parent + overrides from `input`.

```python
result = await ctx.pipeline.run("sqlite/upsert", {
    "table": "products",
    "rows": extracted_rows,
    "unique_keys": ["id"],
})
```

### ctx.artifact

Sandboxed file read/write within the run's work directory. Paths are relative to `ctx.work_dir`. Path traversal is blocked.

#### write(relative_path, data, fmt=None)

```python
await ctx.artifact.write("results.json", {"count": 10})
await ctx.artifact.write("log.txt", "line1\nline2", fmt="text")
```

#### read(relative_path, fmt=None)

```python
data = await ctx.artifact.read("results.json")          # auto-detected JSON
text = await ctx.artifact.read("log.txt", fmt="text")   # explicit text
```

### ctx.utils

Utility methods for external commands and file operations.

#### run_command(command, args=None, *, cwd=None, timeout_ms=30000, env=None)

```python
result = await ctx.utils.run_command("python", ["transform.py", "input.json"])
# {"ok": True, "status": 0, "stdout": "...", "stderr": "..."}

result = await ctx.utils.run_command("curl", ["-s", "https://api.example.com"])
```

#### resolve_path(relative)

Resolve a relative path against `ctx.work_dir`.

```python
full_path = ctx.utils.resolve_path("data/export.csv")
```

#### read_text / write_text / read_json / write_json

Convenience wrappers for common file operations.

```python
ctx.utils.write_text("note.txt", "hello")
text = ctx.utils.read_text("note.txt")

ctx.utils.write_json("data.json", {"x": 1})
data = ctx.utils.read_json("data.json")
```

## Error Handling

Exceptions thrown in `run(ctx)` are caught by the executor. The run status is set to `"failed"` and the error message is recorded.

```python
async def run(ctx):
    try:
        page = await ctx.browser.navigate(url)
    except Exception as e:
        ctx.log(f"Navigation failed: {e}")
        return {"ok": False, "error": str(e)}

    return {"ok": True}
```

## Return Value

The `run(ctx)` function **must** return a plain `dict`. The dict is serialized to `result.json` in the run directory.

```python
# ✅ OK
return {"count": 42, "items": [...]}
return {"ok": True}
return {}

# ❌ NOT OK
return "string"
return 42
return [1, 2, 3]
```

## Input Validation

Input parameters are validated against `meta.input`:

```python
meta = {
    "name": "search/notes",
    "input": {
        "keyword": {"required": True, "description": "Search keyword"},
        "limit": {"required": False, "default": "20"},
    },
}
```

- `keyword` is **required** — the runner raises `ValueError` if missing or empty
- `limit` is **optional** — defaults to `"20"` if not provided
- All values arrive as strings (or whatever JSON type was passed via CLI)

## Best Practices

1. **Use ctx.work_dir for all file paths** — never hardcode absolute paths
2. **Log generously** — `ctx.log()` helps debug failed runs
3. **Return meaningful dicts** — use keys like `count`, `error`, `items` for downstream consumers
4. **Handle browser errors** — pages can crash, timeouts happen
5. **Don't leave pages open** — use `ctx.browser.evaluate()` for quick JS execution (auto-closes the page)
6. **Use sub-pipelines for data persistence** — `ctx.pipeline.run("sqlite/upsert")` handles table creation, dedup, and writes
