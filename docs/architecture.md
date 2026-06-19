# Architecture

## Overview

PipeKit is a Python CLI tool for running composable browser-automation pipelines. It consists of four main components:

```
┌─────────────────────────────────────────────────────────────┐
│                        pipekit CLI                           │
│  pipeline list | info | run                                  │
│  daemon start | status | stop                                │
└──────────────────────┬──────────────────────────────────────┘
                       │ TCP 127.0.0.1:{port}
┌──────────────────────┴──────────────────────────────────────┐
│                      DaemonServer                             │
│                                                               │
│  ┌─────────────────┐  ┌──────────────────────────────────┐  │
│  │ BrowserSession  │  │        PipelineRunner            │  │
│  │                 │  │                                   │  │
│  │ master context  │  │  PipelineDiscover (multi-source)  │  │
│  │   ├── cookies   │  │  PipelineLoader  (.pipeline.py)   │  │
│  │   └── login     │  │  PipelineExecutor (step tracking) │  │
│  │                 │  │                                   │  │
│  │ isolate() ──────┼──│→ PipelineContext                 │  │
│  │   └── new ctx   │  │   ├── ctx.browser                │  │
│  └─────────────────┘  │   ├── ctx.pipeline (sub-calls)   │  │
│                       │   ├── ctx.artifact               │  │
│                       │   └── ctx.utils                  │  │
│                       └──────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## DaemonServer

A long-lived TCP server on `127.0.0.1:{random_port}`. It:

- Manages one `BrowserSession` per account
- Dispatches pipeline commands to `PipelineRunner`
- Auto-shuts down after 10 minutes of inactivity
- Writes `daemon.port` and `daemon.pid` to `~/.pipekit/run/`

The CLI auto-starts the daemon via `ensure_daemon()` on first command.

## BrowserSession

One Chromium process per account. Two modes:

### Managed Mode

`launch_persistent_context(user_data_dir=~/.pipekit/profiles/<name>)`

- Login cookies persist across daemon restarts
- The persistent context becomes the **master context**

### CDP Mode

`connect_over_cdp(http://localhost:9222)`

- Connects to an already-running Chrome
- First existing context becomes the **master context**

### Context Isolation

Every `pipekit run` calls `isolate_with_login()`:

```
master context (persistent, never closed)
  └── storage_state() → {cookies, localStorage}
       └── browser.new_context(storage_state=state)
            └── isolated BrowserContext
                 ├── owns its own tabs/cache/storage
                 ├── pipeline executes
                 ├── sub-pipelines share this context
                 └── closed after run completes
```

This means:
- Login cookies are inherited (no re-login per run)
- Tab pollution is impossible (each run gets a fresh context)
- Concurrent runs are safe (different contexts)

## PipelineRunner

Orchestrates a pipeline run from start to finish.

```
PipelineRunner.run_by_name("sqlite/upsert", input, session)
  │
  ├── 1. PipelineDiscover.resolve("sqlite/upsert")
  │      → scans ~/.pipekit/pipelines/, ./.pipekit/pipelines/, built-in
  │
  ├── 2. PipelineLoader.load(file_path)
  │      → import .pipeline.py, extract meta + run_fn
  │
  ├── 3. Create RunState (run_id, work_dir, input)
  │
  ├── 4. session.isolate_with_login()
  │      → new BrowserContext with master's cookies
  │
  ├── 5. PipelineExecutor.execute(meta, run_fn, run, browser_context)
  │      → creates PipelineContext
  │      → calls run_fn(ctx)
  │      → tracks steps via StepManager
  │      → persists run.json + result.json
  │
  └── 6. BrowserSession.close_context_safe()
         → destroy isolated context, all tabs gone
```

## Pipeline Discovery

Multi-source, priority-ordered:

| Priority | Source | Path |
|----------|--------|------|
| 1 (highest) | Project-local | `./.pipekit/pipelines/` |
| 2 | User-local | `~/.pipekit/pipelines/local/` |
| 3 | Community | `~/.pipekit/pipelines/community/` |
| 4 (lowest) | Built-in | `<site-packages>/pipekit/pipelines/` |

Pipelines are deduplicated by `meta.name`. Higher priority overrides lower.

A pipeline can also be run by explicit file path, bypassing discovery entirely:

```bash
pipekit run ./my-script.pipeline.py --input '{"key":"val"}'
```

## Run Lifecycle

```
RunState:
  status: "running" → "completed" | "failed" | "canceled"
  
  Steps (tracked by StepManager):
    step_001: browser  → navigate to example.com
    step_002: pipeline → sub-pipeline sqlite/upsert
    step_003: artifact → write results.json

  Artifacts (in work_dir):
    run.json      — full run snapshot
    result.json   — pipeline return value
    *.json / *.txt — user artifacts
```

Each run gets its own directory under `<cwd>/.pipekit/runs/<run_id>/`.

## Data Flow

```
CLI input (--input JSON + --flag value pairs)
  │
  ├── Input merging (defaults from meta.input)
  │
  └── PipelineContext.input
       │
       ├── ctx.browser.navigate(url)  → Playwright Page
       ├── ctx.browser.evaluate(js)   → JavaScript result
       ├── ctx.pipeline.run(name)     → sub-pipeline result dict
       ├── ctx.artifact.write/read    → local files
       └── ctx.utils.run_command()    → stdout/stderr
            │
            └── run_fn(ctx) returns dict → result.json
```

## Design Principles

- **Local-first** — all data stores to local SQLite/files by default
- **Composable** — pipelines call pipelines; small tools compose into big workflows
- **Real browser** — Playwright with real Chromium, not simulated requests
- **Isolated by default** — each run gets its own BrowserContext
- **Daemon for efficiency** — browser stays alive between runs
