# Getting Started

## Installation

```bash
pip install pipekit
playwright install chromium
```

Verify:

```bash
pipekit pipeline list
```

You should see the built-in pipelines:

```
[
  {"name": "mongo/upsert", ...},
  {"name": "sqlite/upsert", ...}
]
```

## Your First Pipeline

Create a file `hello.pipeline.py`:

```python
meta = {
    "name": "my/hello",
    "description": "A simple hello-world pipeline.",
    "input": {
        "name": {"required": False, "default": "world"},
    },
}

async def run(ctx):
    name = ctx.input["name"]
    ctx.log(f"Hello, {name}!")
    return {"greeting": f"Hello, {name}!"}
```

Run it by file path:

```bash
pipekit run ./hello.pipeline.py --name "PipeKit"
# {
#   "run_id": "run_1781840705793_b3014003",
#   "pipeline_name": "my/hello",
#   "status": "completed",
#   "result": {"greeting": "Hello, PipeKit!"}
# }
```

Or put it in your project's pipeline directory so you can run it by name:

```bash
mkdir -p .pipekit/pipelines
cp hello.pipeline.py .pipekit/pipelines/

pipekit run my/hello --name "PipeKit"
```

## Using the SQLite Pipeline

PipeKit ships with a built-in SQLite upsert pipeline:

```bash
pipekit run sqlite/upsert \
  --table "users" \
  --rows '[{"id": 1, "name": "Alice", "score": 42}]' \
  --unique_keys '["id"]'
```

Data is written to `~/.pipekit/pipeline-data.db` by default. Query it:

```bash
sqlite3 ~/.pipekit/pipeline-data.db "SELECT * FROM users;"
```

## Browser Pipeline

To use browser features, you need a real pipeline that uses `ctx.browser`:

```python
# screenshot.pipeline.py
meta = {
    "name": "demo/screenshot",
    "input": {
        "url": {"required": True},
    },
}

async def run(ctx):
    page = await ctx.browser.navigate(ctx.input["url"])
    title = await page.title()
    ctx.log(f"Page title: {title}")
    return {"url": ctx.input["url"], "title": title}
```

```bash
pipekit run ./screenshot.pipeline.py --url "https://example.com"
```

The daemon will automatically launch Chrome (or connect to an existing one) and open the URL in an isolated browser context.

## Composing Pipelines

The real power comes from composition:

```python
# search_and_save.pipeline.py
meta = {
    "name": "demo/search_and_save",
    "input": {
        "keyword": {"required": True},
    },
}

async def run(ctx):
    keyword = ctx.input["keyword"]

    # 1. Navigate to search page
    page = await ctx.browser.navigate(f"https://example.com/search?q={keyword}")
    await page.wait_for_selector(".result")

    # 2. Extract data
    results = await page.evaluate("""() => {
        return [...document.querySelectorAll('.result')].map(el => ({
            title: el.querySelector('h3').textContent,
            link: el.querySelector('a').href,
        }));
    }""")

    # 3. Save to SQLite via sub-pipeline
    rows = [{"id": i, "keyword": keyword, **r} for i, r in enumerate(results)]
    db = await ctx.pipeline.run("sqlite/upsert", {
        "table": "search_results",
        "rows": rows,
        "unique_keys": ["id"],
        "update_keys": ["title", "link"],
    })

    ctx.log(f"Saved {db['written_rows']} results")
    return {"count": len(results), "written": db["written_rows"]}
```

## Next Steps

- Read the [Pipeline Reference](pipelines.md) for the full ctx API
- Read the [Architecture](architecture.md) for design details
- Explore the [CLI Reference](cli-reference.md) for all commands
