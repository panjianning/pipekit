# AGENTS.md — PipeKit

AI agent 开发指南。新代码必须遵循本文。

---

## 项目概述

PipeKit 是一个 Python CLI 工具，用于运行可组合的浏览器自动化 Pipeline。核心设计：

- **Pipeline 是公民**：所有功能以 `.pipeline.py` 文件形式存在，`async def run(ctx)` 统一入口
- **多源发现**：项目本地 → 用户本地 → 社区 → 内置，按优先级覆盖
- **隔离执行**：每个 `pipekit run` 通过 `BrowserSession.isolate_with_login()` 获取独立 BrowserContext
- **子 Pipeline 调用**：`ctx.pipeline.run(name, input)` 实现组合

## 目录结构

```
src/pipekit/
├── __init__.py              # __version__
├── __main__.py              # python -m pipekit
├── cli.py                   # argparse CLI，不允许业务逻辑
├── daemon.py                # DaemonServer + client helpers + ensure_daemon
├── browser.py               # BrowserSession：Playwright 生命周期 + 隔离
├── artifact.py              # ArtifactStore：sandboxed 文件读写
│
├── pipeline/                # Pipeline 引擎（核心）
│   ├── __init__.py          # 公开 re-export
│   ├── types.py             # 所有 dataclass：PipelineMeta, RunState, StepState...
│   ├── context.py           # PipelineContext：ctx.browser/pipeline/artifact/utils
│   ├── discover.py          # 多源发现（.pipekit → ~/.pipekit → builtin）
│   ├── loader.py            # Python module 动态加载
│   ├── runner.py            # PipelineRunner：生命周期管理
│   ├── executor.py          # PipelineExecutor：执行 + step 追踪
│   └── step.py              # StepManager
│
└── pipelines/               # 内置 Pipeline（随 pip install 分发）
    ├── sqlite/
    │   └── upsert.pipeline.py
    ├── mongo/
    │   └── upsert.pipeline.py
    └── xhs/
        ├── search.pipeline.py
        ├── note.pipeline.py
        └── search_to_sqlite.pipeline.py

tests/                       # pytest + pytest-asyncio
├── conftest.py              # fixtures: temp_dir, mock_browser_context, mock_browser_session
├── test_types.py
├── test_artifact.py
├── test_loader.py
├── test_discover.py
├── test_context.py
├── test_step.py
├── test_runner.py           # PipelineRunner + PipelineExecutor
├── test_browser.py          # (TODO)
└── test_cli.py              # (TODO)

docs/                        # Markdown 文档
├── README.md
├── getting-started.md
├── pipelines.md
├── cli-reference.md
└── architecture.md
```

## 依赖规则

```
pipeline/types.py    ← 零外部依赖，纯 dataclass
pipeline/step.py     → 依赖 types.py
pipeline/context.py  → 依赖 types.py, artifact.py
pipeline/loader.py   → 依赖 types.py
pipeline/discover.py → 依赖 loader.py, types.py
pipeline/executor.py → 依赖 context.py, step.py, types.py
pipeline/runner.py   → 依赖 discover.py, executor.py, step.py, browser.py
daemon.py            → 依赖 browser.py, pipeline/runner.py
cli.py               → 依赖 daemon.py（只调 send_request / ensure_daemon）

pipelines/*.py       → 通过 ctx API 与引擎交互，不直接 import 引擎内部模块
```

**铁律**：
- `pipeline/types.py` **不能** import 任何项目内模块
- `pipelines/*.py` **不能** import `pipeline/` 内部的任何东西（只能使用 `ctx` 参数）
- `cli.py` **不能**包含业务逻辑，只做参数解析 + 发送请求

## Python 代码规范

### 格式

```toml
# pyproject.toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.format]
quote-style = "double"
```

- 所有文件以 `from __future__ import annotations` 开头
- 使用 `ruff` 零警告
- 公开函数必须有类型注解和 docstring（Google style）

### 类型注解

```python
# ✅ 正确
async def run_by_name(
    self,
    name: str,
    input_data: dict[str, Any],
    session: BrowserSession,
) -> RunState:
    """Run a pipeline discovered by name."""

# ❌ 错误
async def run_by_name(self, name, input_data, session):
```

### 异常处理

```python
# ✅ 正确：重新引发带 from
except ValueError as exc:
    raise RuntimeError(f"Failed: {exc}") from exc

# ❌ 错误：丢失异常链
except ValueError:
    raise RuntimeError("Failed")
```

### 模块组织

- 每个模块单一职责
- `__all__` 导出公开 API（`pipeline/__init__.py` 已做）
- 不在 `__init__.py` 中写业务逻辑

## Pipeline 开发规范

### 文件格式

```python
# my_feature.pipeline.py
from __future__ import annotations

meta = {
    "name": "namespace/name",         # 必填，全局唯一
    "description": "What it does.",   # 推荐
    "tags": ["tag1", "tag2"],         # 推荐
    "input": { ... },                 # 可选
    "output": { ... },                # 推荐
}

async def run(ctx):
    """主入口。必须 async。必须返回 dict。"""
    ...
    return {"ok": True}
```

### 命名规范

- `name` 格式：`<namespace>/<action>`，如 `xhs/search`, `sqlite/upsert`
- 文件名：`<name>.pipeline.py`，放在 `src/pipekit/pipelines/<namespace>/` 下
- 组合 pipeline 放在同一 namespace，如 `xhs/search_to_sqlite`

### ctx API 使用

```python
async def run(ctx):
    # 读取输入（已合并默认值）
    keyword = ctx.input["keyword"]

    # 日志
    ctx.log(f"Processing {keyword}")

    # 浏览器操作
    page = await ctx.browser.navigate("https://example.com")
    result = await ctx.browser.evaluate("document.title")

    # 子 Pipeline 调用
    db = await ctx.pipeline.run("sqlite/upsert", {
        "table": "results",
        "rows": [{"id": 1, "data": result}],
        "unique_keys": ["id"],
    })

    # 文件读写
    await ctx.artifact.write("output.json", data)

    # 外部命令
    cmd = await ctx.utils.run_command("curl", ["-s", url])

    # 返回 plain dict
    return {"count": len(items)}
```

### 内置 Pipeline 编写注意事项

1. **不要硬编码路径**：使用 `ctx.work_dir` 或 `~/.pipekit/`（`Path.home() / ".pipekit"`）
2. **不要 import pipeline 引擎模块**：只通过 `ctx` 访问能力
3. **嵌入 JS 时用 `new RegExp()` 而非 `//` 字面量**：避免 Python 字符串逃逸地狱
4. **添加登录检测**：`document.cookie.includes('a1')` 检查 XHS 登录状态
5. **兼容 AI 搜索灰度**：同时检测 `textarea[name='aiSearchTextarea']` 和 `#search-input`

## 测试规范

```python
# tests/test_xxx.py
class TestSomething:
    def test_happy_path(self, temp_dir: Path) -> None:
        """每个测试独立，使用 temp_dir fixture"""
        ...

    @pytest.mark.asyncio
    async def test_async_behavior(self, temp_dir: Path, mock_browser_context) -> None:
        """异步测试使用 @pytest.mark.asyncio + mock 避免真的启动浏览器"""
        ...
```

- 使用 `pytest` + `pytest-asyncio`
- 异步测试标记 `@pytest.mark.asyncio`
- 使用 `unittest.mock` 避免真实网络/浏览器调用
- `conftest.py` 提供 mock fixtures
- 核心模块覆盖率 ≥ 80%

## CLI 规范

```bash
# 快捷方式（推荐）
pipekit list
pipekit info sqlite/upsert
pipekit run xhs/search --keyword "test"

# 完整路径
pipekit pipeline list|info|run
pipekit daemon start|stop|status
```

- CLI 代码只在 `cli.py` 内
- 新增命令在 `build_parser()` 添加 argparse subparser
- 在 `_run()` 中添加对应 dispatch 逻辑
- 快捷方式（`run`/`list`/`info`）内部映射到 `entity="pipeline"`

## 禁止事项

1. ❌ 在 `cli.py` 中写业务逻辑
2. ❌ Pipeline 文件 import `pipekit.pipeline.*` 内部模块
3. ❌ 在 `pipeline/types.py` 中 import 项目内模块
4. ❌ 硬编码 `resources/` 路径（已废弃）
5. ❌ 使用 JS 正则字面量 `//` 嵌入 Python 字符串（用 `new RegExp()` 代替）
6. ❌ 用 `session.get_master_context()` 执行 pipeline（必须用 `isolate_with_login()` 隔离）
7. ❌ 复制粘贴代码而不抽取共享
8. ❌ 在 Pipeline 的 `run(ctx)` 之外访问 Playwright API 或文件系统

## 关键设计决策

| 决策 | 原因 |
|------|------|
| `src/` 布局 | `pip install -e .` 开发体验 + 明确区分源码和配置 |
| `PipeKit` 命名 | 比 `pw_crawler` 更通用，体现 pipeline toolkit 本质 |
| `pipeline/` 子包 | 引擎代码独立，可单独测试 |
| `pipelines/` 内置目录 | 随 pip 安装分发，不依赖开发期 `resources/` |
| `isolate_with_login()` | 每个 run 独立 BrowserContext，避免 tab/cookie 污染 |
| `ctx` 统一入口 | Pipeline 只依赖 ctx，不 import 引擎内部 |
| `uv` 包管理 | 比 pip 快 10-100x，lock 文件可复现 |
| `hatchling` 构建 | 比 setuptools 简单，PEP 621 标准 |
