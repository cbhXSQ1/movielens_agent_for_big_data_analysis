# Task 1 报告：远端连接配置

计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 1: 远端连接配置」
状态：**DONE**
提交：`8ba18ba` — feat(agent): 远端能力服务连接配置（地址/口令/超时，口令不落日志）

## 文件变更（仅计划允许的两个新文件）

- 新增 `agent/mcp_config.py` — `McpConfig`（`url` / `token` / `timeout_sec` / `interface_version` / `usable` / `health_url()` / `protocol_url()` / `safe_dict()`）与 `load(overrides=None)`；配置来源为 `config/mcp.settings.json`（可选）+ 环境变量 `AGENT_MCP_*`，`overrides` 最后覆盖。超时默认 15.0s、上限 120.0s。
- 新增 `agent/tests/test_mcp_config.py` — 3 个用例（默认不可用、环境变量覆盖且可用、`safe_dict()` 脱敏）。

## 测试命令

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests/test_mcp_config.py -v
```

（工作目录：`E:\学\大数据分析\movielens_agent_for_big_data_analysis`）

## 测试输出

RED（实现前，确认失败原因正确）：

```
collecting ... collected 0 items / 1 error
E   ImportError: cannot import name 'mcp_config' from 'agent'
1 error in 0.36s
```

GREEN（实现后）：

```
collected 3 items

agent/tests/test_mcp_config.py::test_defaults_unusable_without_url_and_token PASSED [ 33%]
agent/tests/test_mcp_config.py::test_env_overrides_and_usable PASSED     [ 66%]
agent/tests/test_mcp_config.py::test_safe_dict_hides_token PASSED        [100%]

============================== 3 passed in 0.10s ==============================
```

**3 passed**。

## TDD 过程

1. 写 `agent/tests/test_mcp_config.py`（含既有测试同款的 `sys.path.insert(0, REPO_ROOT)` 前置，匹配 `agent/tests/` 的导入约定）。
2. 跑测试 → ImportError（`agent.mcp_config` 不存在），失败原因与预期一致。
3. 实现 `agent/mcp_config.py`（逐字采用计划给定的值）。
4. 跑测试 → 3 passed。
5. 单次本地提交，未推送。

## 红线核对

- 口令（`AGENT_MCP_TOKEN`）绝不写入日志/信封/用户可见输出：`safe_dict()` 仅返回 `token_set: bool`；测试 `test_safe_dict_hides_token` 断言原令牌串不出现在 `str(d)` 中。
- 未硬编码任何令牌（配置只来自文件/环境变量/显式 `overrides`）。

## 关注点 / concerns

1. **环境变更（非代码）**：指定解释器 `.scratch\langgraph-probe\.venv` 原先**未安装 pytest**，已用 `python -m pip install pytest` 装入（pytest 9.1.1）。该 venv 已含 `mcp 2.3.0`、`httpx 0.28.1`（供后续 Task 2 使用）。未改动仓库依赖文件。
2. **`config/mcp.settings.json` 目录**：`CONFIG_PATH` 指向仓库根 `config/`，该目录当前不存在时 `_read_file()` 安全返回 `{}`，不影响可用性判定。
3. 本任务仅覆盖 Task 1；未触碰其它文件。
