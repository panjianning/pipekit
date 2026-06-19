# CLI Reference

## Global Options

| Option | Description |
|--------|-------------|
| `--version` | Show version and exit |
| `--account <name>` | Browser profile account name (default: `default`) |
| `--cdp <port>` | Connect to existing Chrome via CDP port |

## pipeline list

List all discoverable pipelines.

```bash
pipekit pipeline list
```

Output:

```json
[
  {
    "name": "mongo/upsert",
    "description": "Batch upsert rows into a MongoDB collection.",
    "tags": ["storage", "database", "mongodb"],
    "source": "builtin",
    "file_path": "/path/to/upsert.pipeline.py"
  }
]
```

## pipeline info

Show details of a pipeline including input/output schemas.

```bash
pipekit pipeline info sqlite/upsert
```

## pipeline run

Run a pipeline by name or file path.

### By name

```bash
pipekit pipeline run sqlite/upsert \
  --input '{"table":"demo","rows":[{"id":1}],"unique_keys":["id"]}'
```

### By file path

```bash
pipekit pipeline run --file ./my-script.pipeline.py \
  --input '{"keyword":"test"}'
```

### With CLI flags

Flags after `--input` are parsed as pipeline arguments:

```bash
pipekit pipeline run sqlite/upsert \
  --table "users" \
  --unique_keys '["id"]'
```

| Option | Description |
|--------|-------------|
| `name` | Pipeline name (optional if `--file` is used) |
| `--input <json>` | JSON object with pipeline input |
| `--file <path>` | Explicit `.pipeline.py` file path |

## daemon

Manage the browser daemon process.

### daemon start

Start the daemon (auto-started on first `pipeline` command, usually not needed).

```bash
pipekit daemon start
```

### daemon status

Show daemon process info and active browser sessions.

```bash
pipekit daemon status
```

Output:

```json
{
  "ok": true,
  "pid": 12345,
  "sessions": [
    {"key": "default", "idle_seconds": 12.3}
  ]
}
```

### daemon stop

Gracefully stop the daemon and all browser sessions.

```bash
pipekit daemon stop
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPEKIT_HOME` | `~/.pipekit` | PipeKit data directory |
| `CHROME_PATH` | auto-detected | Path to Chrome/Chromium binary |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Pipeline failed or CLI error |

## Run Output

Successful run:

```json
{
  "run_id": "run_1781840705793_b3014003",
  "pipeline_name": "sqlite/upsert",
  "status": "completed",
  "result": { "ok": true, "written_rows": 5 },
  "steps": 0,
  "errors": []
}
```

Failed run:

```json
{
  "run_id": "run_1781840705793_b3014003",
  "pipeline_name": "my/broken",
  "status": "failed",
  "result": null,
  "errors": ["ValueError: missing required input: keyword"]
}
```

## Run Artifacts

Each run creates a directory under `<cwd>/.pipekit/runs/<run_id>/`:

```
runs/run_1781840705793_b3014003/
├── run.json       # Full run snapshot (status, steps, logs)
├── result.json    # Pipeline return value
└── ...            # User artifacts (ctx.artifact.write)
```
