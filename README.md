<div align="center">
  <h1>🔗 PipeKit</h1>
  <p>
    <strong>Composable browser-automation pipelines for Python.</strong>
  </p>
  <p>
    <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
    <img src="https://img.shields.io/badge/tests-62%20passed-brightgreen.svg" alt="Tests">
  </p>
</div>

## What can you do with it

```bash
# Search XHS (input simulation, XHR interception, scroll pagination)
pipekit run xhs/search --keyword "露营装备" --num_pages 2

# Search Google (simulated input + DOM parsing, anti-detection)
pipekit run google/search --query "OpenAI" --pages 1

# Get XHS note details (title, body, author, stats)
pipekit run xhs/note --url "https://www.xiaohongshu.com/explore/..."

# Search XHS → auto-save to SQLite (sub-pipeline composition)
pipekit run xhs/search_to_sqlite --keyword "咖啡推荐" --num_pages 3

# Store any data in SQLite (auto-create table + upsert)
pipekit run sqlite/upsert --table "products" --rows '[{"id":1,"name":"widget"}]' --unique_keys '["id"]'

# Batch upsert into MongoDB
pipekit run mongo/upsert --database "mydb" --collection "notes" --rows '[...]' --unique_keys '["note_id"]'
```

## Install

```bash
pip install pipekit
playwright install chromium
```

That's it. No config files, no API keys.

## Write your own pipeline

Create `hello.pipeline.py`:

```python
meta = {
    "name": "my/hello",
    "input": {"name": {"required": False, "default": "world"}},
}

async def run(ctx):
    name = ctx.input["name"]
    ctx.log(f"Hello, {name}!")
    return {"greeting": f"Hello, {name}!"}
```

```bash
pipekit run ./hello.pipeline.py --name "PipeKit"
```

## Compose pipelines

```python
meta = {"name": "my/scrape_and_save"}

async def run(ctx):
    # 1. Open a page
    page = await ctx.browser.navigate("https://example.com")
    title = await page.title()

    # 2. Save to SQLite via built-in sub-pipeline
    await ctx.pipeline.run("sqlite/upsert", {
        "table": "scraped",
        "rows": [{"id": 1, "title": title}],
        "unique_keys": ["id"],
    })
    return {"saved": True}
```

## ctx API

Every `async def run(ctx)` receives:

| Property | Description |
|----------|-------------|
| `ctx.input` | Merged input parameters |
| `ctx.log(msg)` | Append to run log |
| `ctx.browser.navigate(url)` | Open page → returns Playwright Page |
| `ctx.browser.evaluate(script)` | Run JS in a fresh page |
| `ctx.pipeline.run(name, input)` | Call a sub-pipeline |
| `ctx.artifact.write/read(path, data)` | Sandboxed file I/O |
| `ctx.utils.run_command(cmd, args)` | Execute external command |
| `ctx.utils.read_json/write_json(path)` | Quick JSON I/O |
| `ctx.new_page()` | Raw Playwright Page access |

## Built-in pipelines

| Pipeline | What it does |
|----------|-------------|
| `xhs/search` | Search XHS (input simulation + XHR interception, supports AI search) |
| `xhs/note` | Fetch XHS note details (title, body, author, stats, images) |
| `xhs/search_to_sqlite` | Search XHS → auto-save to SQLite |
| `google/search` | Search Google (input simulation + DOM parsing, anti-detection) |
| `sqlite/upsert` | Auto-create table + upsert rows |
| `mongo/upsert` | Batch upsert into MongoDB |

## Multiple accounts

Each account is an independent Chrome profile with its own login state:

```bash
# Default account (auto-managed Chrome)
pipekit run xhs/search --keyword "camping"

# Separate account — own profile, own login, own Chrome window
pipekit --account xhs-main run xhs/search --keyword "coffee"

# Connect to an existing Chrome you already have open
pipekit --cdp 9222 run google/search --query "hello"
```

Under the hood:

```
--account default  →  ~/.pipekit/profiles/default/  →  Chrome on 127.0.0.1:199XX
--account xhs-main →  ~/.pipekit/profiles/xhs-main/ →  Chrome on 127.0.0.1:199YY
--cdp 9222         →  connect_over_cdp → uses first existing context as master
```

Each account's debug port is persisted to disk and reused across restarts.

## How it works

```
pipekit run → Daemon → Chrome (subprocess, no automation flags)
                         │
                         ├── master context (persistent, login cookies)
                         └── isolate() → fresh BrowserContext per run
                                           ├── pipeline executes
                                           └── destroyed on completion
```

- Chrome launched via **raw binary** (not Playwright managed) — no `--enable-automation` flag
- **Isolated BrowserContext** per run — tabs/cookies don't leak between pipelines
- **Daemon** keeps Chrome alive between commands
- **Login persists** across restarts (graceful CDP shutdown flushes cookies to disk)

Pipeline discovery (highest priority first):
```
1. Explicit path:  pipekit run ./my.pipeline.py
2. Project-local:  ./.pipekit/pipelines/
3. User-local:     ~/.pipekit/pipelines/local/
4. Built-in:       <site-packages>/pipekit/pipelines/
```

## CLI

```bash
pipekit list                          # List all pipelines
pipekit info <name>                   # Show input/output schema
pipekit run <name> [--input '{}']     # Run by name
pipekit run --file ./my.pipeline.py   # Run by path

pipekit --account <name> run ...      # Use a specific Chrome profile
pipekit --cdp <port> run ...          # Connect to existing Chrome

pipekit daemon start|stop|status      # Manage browser daemon
```

## License

MIT © Tokex
