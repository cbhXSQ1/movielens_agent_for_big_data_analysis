# Agent Task 3 报告 —— 远端工具表与来源改写（合并计划 Task 3 + Task 6）

- 计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 3: 远端工具表与来源改写」+「### Task 6: 参数名与默认值对齐」
- 状态：DONE
- 日期：2026-10-18

## 目标（本任务）

把 Agent 的工具表改为**以远端能力自描述为权威**：22 项远端能力标 `source=mcp`，
本机只保留 `quick_clean_demo`（`source=local`）；参数名/默认值/枚举/取值范围
与 `hadoop/mcpserver/tools.py` 的 `TOOLS` 逐项对齐。

## 改动的文件

### 计划内（本任务指定）

| 文件 | 变更 |
| --- | --- |
| `agent/mcp_tools.py` | 新增。`REMOTE_TOOL_SPECS`（22 项，`source=mcp`）、`LOCAL_ONLY_TOOLS`、`remote_tool_names()`、`spec_for(name)` |
| `agent/tool_specs.py` | `TOOL_SPECS` 定义后合并远端规格；本机仅剩 `quick_clean_demo`；清理两处「口径」用词 |
| `agent/runtime/stream.py` | 工具表推送改用规格自带的 `long_running` / `estimated_seconds`（规格缺省才回退 `LONG_RUNNING_TOOLS`）；`_param_entry` 默认值优先取 schema 声明（远端权威），缺省回退实现签名 |
| `agent/tool_policy.py` | 为 12 项新增远端工具补策略条目（只读 11 项 + `cancel_task`） |
| `agent/tests/test_mcp_tools.py` | 新增。12 条用例（22 项 / 来源 / 参数对齐 / 长耗时） |
| `agent/tests/test_tool_specs.py` | schema-实现一致性检查改为**仅对本机来源**生效；工具表总数、枚举、limit 边界按远端语义更新 |

### 计划外（为满足「全量套件绿」的必要连带改动）

> 说明：来源改写把 10 个原「本地」工具改为远端、并把参数 `n→limit`、`fmt→format`
> 后，既有集成用例按旧语义断言会失败。计划自身把这些收口放在 Task 14
> （「改既有测试与全量跑通」），但本任务要求**现在**全量绿，故对以下用例做
> 最小语义更新（不改被测生产逻辑，只把断言对齐远端权威语义）。

| 文件 | 变更 |
| --- | --- |
| `agent/tests/test_stage2_tool_specs.py` | 「全部本地」改为「仅 quick_clean_demo 本地」；`tools_by_source`、schema==签名 检查只针对本机来源 |
| `agent/tests/test_tool_policy.py` | `read_only` 精确集合补入 11 项新增只读远端工具 |
| `agent/tests/test_stage3b_stream.py` | `get_samples` 默认参数断言 `n` → `limit`（默认 5） |
| `agent/tests/test_stage5_offline.py` | 离线默认值/校验用例 `n` → `limit`；越界值 999 → 9999（新上限 2000） |
| `agent/tests/test_stage6_contract.py` | `/health` 工具来源并集 `["local"]` → `["local","mcp"]`；默认值断言 `n` → `limit` |
| `agent/tests/test_stage7_tree.py` | HTTP 离线路径用的 runtime 注入假后端（`list_tasks` 已改判远端，默认会走未接入的 MCP 后端） |
| `agent/tests/test_stage8_mcp_seam.py` | 「本地来源」示例工具由 `list_schemes` 改为唯一的本机工具 `quick_clean_demo`；默认路由断言改为「仅快速演示走本地」 |

## 关键实现点

- `agent/mcp_tools.py` 的 22 项规格**逐项照 `hadoop/mcpserver/tools.py` 的 `TOOLS` 转写**：
  参数类型（`int→integer`、`bool→boolean`、`enum→string`+`enum`）、默认值、
  枚举顺序、`minimum`/`maximum`、必填、`long_running`、`estimated_seconds` 全一致。
  已用脚本对 `hadoop.mcpserver.tools.TOOLS` 逐字段比对 → **MISMATCHES: NONE**。
- 关键项：`get_samples` 用 `limit`（默认 5，范围 0..2000）、`sample_type` 默认 `cleaned`、
  `table` 默认 `movies`；`get_report` 用 `format`（枚举 `["md","json"]`，默认 `json`）；
  `start_cleaning_task`/`start_clean_task`/`score_task` 无 `foreground`、无 `exec_mode`。
- `tool_specs.py` 采用**覆盖式合并**（`TOOL_SPECS[name] = 远端规格`），
  `SPEC_TO_FUNCTION` 保留（远端项继续映射，本机 `quick_clean_demo` 在册）。
- `stream.tool_table()` 的长耗时字段：规格声明了 `long_running` 就以规格为准，
  否则回退 `LONG_RUNNING_TOOLS`；非长耗时工具的 `estimated_seconds` 仍为 `None`
  （保持既有消费者与前端语义）。
- `stream._param_entry`：默认值优先取 schema 的 `default`（远端权威），
  本机专属工具（如 `quick_clean_demo.quiet`）回退实现签名默认值。
- 用户可见文案：新增/改写文本均不含「冒烟 / 对账 / 口径」；
  顺带清掉 `tool_specs.py` 里两处历史「口径」用词。

## 测试命令与结果

定向（本任务新增 + 改造用例）：

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests/test_mcp_tools.py agent/tests/test_tool_specs.py -v
```

结果：`20 passed, 59 subtests passed in 0.08s`

全量（无网络、不连远端服务）：

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q
```

结果：`329 passed, 186 subtests passed in 38.89s`（基线为 `316 passed, 116 subtests`）

规格一致性核对（本地脚本，导入两端契约逐字段比对）：

```
...\python.exe -c "import sys; sys.path.insert(0,'.'); from agent import mcp_tools; from hadoop.mcpserver import tools as ht; ..."
```

结果：`counts 22 22` / `agent-only set()` / `server-only set()` / `MISMATCHES: NONE`

## 关注点 / 备注

1. **文件范围超出任务给定的「Only touch」清单**：为使全量套件绿，除计划内 6 个文件外，
   额外最小改动了 7 个既有测试文件（见上表「计划外」）。生产代码仍严格限定在
   `mcp_tools.py` / `tool_specs.py` / `runtime/stream.py` / `tool_policy.py`，
   未触碰 `agent/tools.py` 的本地实现函数（按任务要求保留）。
2. **来源改写带来的默认运行期行为变化（预期）**：10 个原本地工具改为 `source=mcp` 后，
   默认 `build_tool_backend()` 的路由会把它们交给 `McpToolBackend`；真实客户端要到
   计划 Task 9 才在运行期装配。在此之前，不注入后端的调用会得到 `MCP_UNAVAILABLE`
   （诚实报错）。`test_stage7_tree.py` 的 HTTP 用例因此注入假后端。
3. **`wait_for_task` 被远端声明为长耗时**：`stream.is_long_running("wait_for_task")`
   随之返回 `True`、预计 300 秒。这遵循「以规格为准」；若后续认为「阻塞等待」不应
   登记为后台长任务，可在 Task 8/9 收口时单独讨论。
4. **旧本地 spec 字面量仍留在 `tool_specs.py`**（被远端规格覆盖，运行期不可达）：
   按关键决策 #3 采用覆盖式合并，未删旧定义以免过度改动；如需彻底清理可后续单独处理。
