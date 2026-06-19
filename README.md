<div align="center">
  <h1>🔗 PipeKit</h1>
  <p>
    <strong>Pipeline toolkit — composable browser-automation pipelines for Python.</strong>
  </p>
  <p>
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
    <img src="https://img.shields.io/badge/tests-62%20passed-brightgreen.svg" alt="Tests">
  </p>
</div>

---

## What is it

PipeKit is a CLI tool that runs composable, browser-automated pipeline scripts. Each pipeline is a standalone Python file — write once, run anywhere, compose with others.

```
pipekit run sqlite/upsert --input '{"table":"products","rows":[...]}'
pipekit run ./my-crawler.pipeline.py --keyword "camping"
```

### Why PipeKit

| You want to | PipeKit does |
|-------------|-------------|
| Scrape a website and save to SQLite | Write a pipeline → `ctx.browser.navigate()` → `ctx.pipeline.run("sqlite/upsert")` |
| Chain multiple data sources | Pipeline A calls Pipeline B calls Pipeline C |
| Schedule recurring data collection | `cron` + `pipekit run` |
| Keep browser sessions alive between runs | Daemon mode — one Chrome, many isolated runs |

**All data stays local. No cloud, no SaaS.**

---

## Quick Start

### Install

```bash
pip install pipekit
playwright install chromium
```

### Your first pipeline

Create `hello.pipeline.py`:

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

Run it:

```bash
pipekit run ./hello.pipeline.py --name "PipeKit"
```

### Navigate a browser

```python
meta = {
    "name": "example/screenshot",
    "input": {
        "url": {"required": True, "description": "Page URL"},
    },
}

async def run(ctx):
    page = await ctx.browser.navigate(ctx.input["url"])
    await ctx.artifact.write("page.json", {"title": await page.title()})
    return {"ok": True}
```

### Compose pipelines

```python
meta = {"name": "scrape/save"}

async def run(ctx):
    # 1. Scrape data
    page = await ctx.browser.navigate("https://example.com")
    data = await page.evaluate("() => document.title")

    # 2. Call built-in pipeline
    await ctx.pipeline.run("sqlite/upsert", {
        "table": "scraped",
        "rows": [{"id": 1, "title": data}],
        "unique_keys": ["id"],
    })
    return {"saved": True}
```

---

## CLI Reference

```bash
pipekit pipeline list                # List all discoverable pipelines
pipekit pipeline info <name>         # Show pipeline input/output schema
pipekit pipeline run <name>          # Run by name
pipekit pipeline run --file <path>   # Run by file path

pipekit daemon start                 # Start browser daemon (auto-started on first run)
pipekit daemon status                # Show daemon + sessions
pipekit daemon stop                  # Graceful shutdown
```

Pipeline arguments can be passed as `--input '{"key":"val"}'` or as CLI flags:

```bash
pipekit run sqlite/upsert --table "users" --rows '[{"id":1}]' --unique_keys '["id"]'
```

---

## ctx API

Every `async def run(ctx)` receives a context object with:

| Property | Description |
|----------|-------------|
| `ctx.input` | Merged & validated input parameters |
| `ctx.work_dir` | Absolute path to this run's working directory |
| `ctx.log(msg)` | Append a line to the run log |
| `ctx.browser.navigate(url)` | Open new page, navigate, return Page |
| `ctx.browser.evaluate(script)` | Execute JS in a fresh page |
| `ctx.pipeline.run(name, input)` | Call a sub-pipeline by name |
| `ctx.artifact.write(path, data)` | Write file in sandboxed work_dir |
| `ctx.artifact.read(path)` | Read file from sandboxed work_dir |
| `ctx.utils.run_command(cmd, args)` | Execute external command |
| `ctx.utils.resolve_path(rel)` | Resolve relative path → absolute |
| `ctx.utils.read_text/write_text(path, data)` | Quick text file I/O |
| `ctx.utils.read_json/write_json(path, data)` | Quick JSON file I/O |
| `ctx.new_page()` | Raw access: create a Playwright Page |

---

## Pipeline Discovery

PipeKit finds pipelines from multiple sources (highest priority first):

```
1. Explicit file path:  pipekit run ./my-script.pipeline.py
2. Project-local:       ./.pipekit/pipelines/
3. User-local:          ~/.pipekit/pipelines/local/
4. Community:           ~/.pipekit/pipelines/community/
5. Built-in:            <site-packages>/pipekit/pipelines/
```

### Built-in pipelines

| Pipeline | Description |
|----------|-------------|
| `sqlite/upsert` | Auto-create table + upsert rows into SQLite |
| `mongo/upsert` | Batch upsert rows into MongoDB |

---

## Pipeline File Spec

```python
# my_pipeline.pipeline.py
from __future__ import annotations

meta = {
    "name": "namespace/name",           # Required: globally unique
    "description": "What it does.",     # Optional
    "tags": ["db", "crawler"],          # Optional
    "input": {
        "keyword": {
            "required": True,
            "description": "Search keyword",
        },
        "limit": {
            "required": False,
            "default": "20",
            "description": "Max results",
        },
    },
    "output": {
        "count": {"type": "int", "description": "Number of results"},
    },
}

async def run(ctx):
    """Main entry point. Must be async. Must return a dict."""
    keyword = ctx.input["keyword"]
    # ... do work ...
    return {"count": 42}
```

---

## Browser Isolation

Every `pipekit run` gets its own isolated BrowserContext:

```
pipekit run pipeline-A
  └── isolate_with_login()  ← clones master's cookies
       └── BrowserContext-A  ← independent tabs/cache/storage
            └── pipeline executes
            └── sub-pipelines share same context
            └── context destroyed on completion

pipekit run pipeline-B
  └── isolate_with_login()  ← fresh clone
       └── BrowserContext-B  ← no pollution from A
```

Login cookies persist in the **master context** (survives daemon restarts via Chrome profile).

---

## Configuration

| Env variable | Default | Description |
|-------------|---------|-------------|
| `PIPEKIT_HOME` | `~/.pipekit` | PipeKit home directory |
| `CHROME_PATH` | auto-detected | Path to Chrome binary |

Directory layout under `~/.pipekit/`:

```
~/.pipekit/
├── profiles/default/     # Chrome user profile (login cookies)
├── pipelines/
│   ├── local/            # Your custom pipelines
│   └── community/        # Community-shared pipelines
├── run/                  # Daemon runtime files
│   ├── daemon.port
│   ├── daemon.pid
│   └── daemon.log
└── pipeline-data.db      # Default SQLite database
```

---

## Architecture

```
pipekit run → CLI → ensure_daemon() → TCP 127.0.0.1:{port}
                                         ↓
DaemonServer                              ↓
├── PipelineRunner                        ↓
│   ├── PipelineDiscover (multi-source)   ↓
│   ├── PipelineLoader                   ↓
│   └── PipelineExecutor                 ↓
│       ├── PipelineContext              ↓
│       │   ├── ctx.browser  → Playwright Page
│       │   ├── ctx.pipeline → sub-pipeline
│       │   ├── ctx.artifact → file I/O
│       │   └── ctx.utils    → shell commands
│       └── StepManager
└── BrowserSession
    ├── master context (persistent, login cookies)
    └── isolate_with_login() → new BrowserContext per run
```

---

## License

MIT © Tokex
