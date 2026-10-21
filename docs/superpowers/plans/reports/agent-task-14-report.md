# Task 14 报告：收口 —— 改既有测试与全量跑通

## 状态
完成（本地全绿；未推送）。提交 `95c2332639172a9434ae3f9d9d1c5dfcfb2f38f8`
（`test(agent): 收口 —— 全量用例按远端语义通过；补端到端假客户端用例与受限文案清理`）。

## 目标
1. 全量跑通 Agent 侧测试，修掉任何仍假设「本地 driver / 旧参数」语义的遗留用例；
2. 用假 MCP 客户端补一条端到端本地用例：发起长任务 → 查状态 → 终态 → 取结果；
3. 清理 Agent 侧受限文案（冒烟 / 对账 / 口径），保留原意；
4. 修正 `agent/runtime/stream.py` 的 `LONG_RUNNING_TOOLS` 兜底值。

## 1. 全量套件（无需再改遗留用例）
基线（改动前）：

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q
396 passed, 237 subtests passed in 43.96s
```

结论：AG-3 阶段已把遗留用例调整到远端语义，本轮全量基线已经全绿，**没有**发现
仍按本地 driver / 旧参数断言的遗留用例，因此未再改动既有用例的断言（未削弱覆盖面）。

## 2. 新增端到端本地用例（假 MCP 客户端，无网络）
新增 `agent/tests/test_e2e_remote_long_task.py`，其中显式定义本测试的替身
`FakeRemoteClient(mcp_contract.McpClient)`（**假 MCP 客户端**，不连网、不访问
127.0.0.1:8900），受控返回「发起 / 查状态 / 取结果」三类信封。

链路（全部经运行时工具后端，不绕过路由）：

1. `Runtime(mcp_client=FakeRemoteClient(), task_probe=RemoteTaskProbe(...))`；
2. `run_offline(..., "start_cleaning_task")` → `RoutingBackend` 按 `source=mcp` 路由到
   `McpToolBackend` → 假客户端；`LongTaskBackend` 登记长任务；
3. 断言长任务已登记：`agent_task_id`（`LT-xxxx`）+ `hadoop_task_id`（`H-9001`）写入
   对话上下文，状态 `running`；
4. 改假客户端进度 → `poller.tick()` → `get_task_status` 返回的 `progress_percent` /
   `stage` 同步进记录（进度流动）；
5. 置终态 `succeeded` → `poller.tick()` → 经 `get_task_result`（按 **hadoop_task_id**）
   取回权威结果，记录进 `succeeded`、`result` 非空；
6. 断言事件：`task_status`、`result_payload`、`artifact_added`、`message_done`；
7. 断言结果/产物真的产出：`result_payload.blocks.data_volume` 存在；产物列表含
   `cleaned/movies/movies.dat`、`report.json`（来自 `get_task_result.artifacts[]`）。

确定性说明：`provider_factory` 固定抛 `ModelError(NOT_CONFIGURED)`，终态走
`_degrade_continuation` 的确定性收尾（不触模型）；登记后即 `stop_polling()`，
后续用受控 `poller.tick()` 推进，避免后台线程时序干扰。

单测结果：

```
... -m pytest agent/tests/test_e2e_remote_long_task.py -v
1 passed in 0.62s
```

## 3. 受限文案清理（保留原意；不改代码标识符）
用户可见文案：

| 文件:行 | 改前 | 改后 |
|---|---|---|
| `agent/cli.py:55` | 运行口径：…（默认，正式口径） | 运行方式：…（默认，正式运行） |
| `agent/explain.py:193` | 口径提醒：分数提升… | 提醒：分数提升… |
| `agent/README.md:45` | 样本口径 | 样本模式 |
| `agent/README.md:73` | 附一句口径提醒 | 附一句提醒 |
| `agent/README.md:102` | 建议统一口径 | 建议统一说法 |

内部注释 / 文档字符串（Agent 侧人读文本，非用户可见输出）：

| 文件:行 | 改前 | 改后 |
|---|---|---|
| `agent/constants.py:19` | 评分口径的唯一产地 | 评分标准的唯一产地 |
| `agent/gateway/__init__.py:8` | 边界口径 | 边界约定 |
| `agent/http_api.py:69` | 运行口径参数 | 运行参数 |
| `agent/http_api.py:98` | 运行口径键 | 运行参数键 |
| `agent/http_api.py:100` | 口径快照 | 运行参数快照 |
| `agent/http_api.py:229` | 本轮口径 | 本轮运行参数 |
| `agent/http_api.py:1126` | 口径快照 | 运行参数快照 |
| `agent/http_api.py:1139` | 显式指定的口径 | 显式指定的运行参数 |
| `agent/llm/config.py:11` | 同口径 | 同约定 |
| `agent/llm_config.py:35` | 定的口径 | 定的约定 |
| `agent/mcp_contract.py:190` | 出现「冒烟」「对账」「口径」等词 | 出现行业黑话等受限词 |
| `agent/protocol.py:72` | 落口径快照用 | 落运行参数快照用 |
| `agent/resources.py:102` | 与 driver 同口径 | 与 driver 同约定 |
| `agent/runtime/artifacts_ref.py:466` | 对话口径快照 | 对话运行参数快照 |
| `agent/runtime/graph.py:16` | 同口径 | 同约定 |
| `agent/runtime/graph.py:38` | 同口径 | 同约定 |
| `agent/runtime/runtime.py:167` | 运行口径快照 | 运行参数快照 |
| `agent/runtime/runtime.py:174` | 真实生效的口径 | 真实生效的运行参数 |
| `agent/runtime/store.py:14` | 直调口径快照 | 直调参数快照 |
| `agent/runtime/tool_exec.py:6` | 同口径 | 同约定 |
| `agent/tests/test_stage3_conversation.py:5` | 直调口径 | 直调参数 |
| `agent/tools.py:25,26` | 运行口径 / 正式口径 | 运行范围 / 正式运行 |
| `agent/tools.py:62` | 全量正式口径 | 全量正式运行 |
| `agent/tools.py:68` | 与 driver 口径一致 | 与 driver 约定一致 |

保留：`agent/tests/test_mcp_errors.py` 的 `FORBIDDEN_WORDS = ("冒烟", "对账", "口径")`
及其说明 —— 这是**规则本身的执行用例**，不是面向用户的文案。`git grep` 后 Agent 侧
其余位置已无这三个词（仅该用例保留）。

`agent/README.md` 示例里仍有历史 CLI 参数（`--foreground` 等），但不在本次清理范围，
未改动代码/参数名。

## 4. `LONG_RUNNING_TOOLS` 兜底值修正
`agent/runtime/stream.py`：

```diff
 LONG_RUNNING_TOOLS = {
-    "start_cleaning_task": 900,
-    "start_clean_task": 420,
+    "start_cleaning_task": 1500,
+    "start_clean_task": 900,
     "score_task": 300,
 }
```

这些值实际被工具规格（`mcp_tools.REMOTE_TOOL_SPECS` 的 `estimated_seconds`）遮蔽，
但兜底字面量不应与规格矛盾。新增守卫用例
`agent/tests/test_mcp_tools.py::TestParamAlignment::test_stream_fallback_matches_remote_estimates`
断言三者的兜底值等于远端规格值。原有用例只断言规格值（1500/900/300），未断言旧兜底值。

## 最终验证（精确命令与结果）

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q
398 passed, 240 subtests passed in 74.36s (0:01:14)
```

（基线 396 passed / 237 subtests；净增 2 项：端到端 1 项 + 兜底值守卫 1 项。
subtests 净增 3：兜底值守卫的 3 个 subtests。）

## 关切 / 备注
- 端到端用例全程无网络：假客户端、假结果、确定性收尾；未访问 127.0.0.1:8900。
- 端到端用例使用 `run_offline(..., facts={}, enforce_policy=False)` 走运行时同一执行
  后端 / 长任务装饰器；策略校验与本次「远端语义收口」正交，故关闭以保持确定性。
- 背景轮询线程在登记后立即停止，进度/终态由受控 `poller.tick()` 驱动，避免时序抖动。
- `LONG_RUNNING_TOOLS` 兜底值被规格遮蔽，实际运行仍以规格值为准；本次只消除字面矛盾。
- 本次提交仅含 `agent/` 文件；`docs/`、`config/`、根 `README.md` 等非 Agent 侧文案未动。
