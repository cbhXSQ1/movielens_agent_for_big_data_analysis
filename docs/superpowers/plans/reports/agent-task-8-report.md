# Agent Task 8 报告 —— 长任务探针改走远端（合并 Task 7 状态五态含中止）

- 计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 8: 长任务探针改走远端」+「### Task 7: 状态五态含中止」（本次刻意合并）
- 状态：DONE
- 日期：2026-10-18

## 改动的文件

| 文件 | 变更 |
| --- | --- |
| `agent/runtime/long_task.py` | 新增 `RemoteTaskProbe(client)`；新增 `creates_task(tool_name)` 与 `LongTaskManager.creates_task` |
| `agent/runtime/tool_exec.py` | `LongTaskBackend` 登记判据由 `is_long_running` 改为 `creates_task` |
| `agent/explain.py` | 抽出 `STATUS_CN` 与 `status_label()`，映射补 `aborted → 已中止`，`explain_status` 改用它 |
| `agent/tools.py` | `run_and_wait` 的等待终态集合补 `aborted` |
| `agent/runtime/__init__.py` | 导出 `RemoteTaskProbe`、`creates_task` |
| `agent/tests/test_remote_probe.py` | 新增。远端探针 5 条 + 登记修复 2 条 |
| `agent/tests/test_task_status_aborted.py` | 新增。状态五态 3 条 |

## 实现要点

- `RemoteTaskProbe(client)` 与 `DriverTaskProbe` **同形状**（`probe(record) -> dict | None`）：
  用 `record["hadoop_task_id"]` 经 `client.call_tool("get_task_status", {"task_id": ...})`
  查状态；成功终态再 `call_tool("get_task_result", ...)` 取权威结果拼进 `seen["result"]`。
  缺 `hadoop_task_id`、传输异常、信封 `ok=false` 一律返回 `None`，绝不抛。
- **双编号纪律**：远端只读查询一律用 `hadoop_task_id`；`agent_task_id` 不参与远端调用。
- 登记修复：只有**任务创建类**工具（`TOOL_TASK_NAMES`：`start_cleaning_task` /
  `start_clean_task` / `score_task`）才登记长任务。`wait_for_task` 虽被标为长耗时
  （`is_long_running=True`），但只是阻塞等待，不再被误登记。`is_long_running`（展示标记）
  语义与行为保持不变。
- 状态五态：`explain.STATUS_CN` / `status_label()` 含 `aborted:"已中止"`；`tools.run_and_wait`
  遇 `aborted` 立即返回（不再空转到超时）。
- 未接网：`RemoteTaskProbe` 只在测试中用**注入的假客户端**验证；未改动
  `http_api` / `runtime` 的装配点（留待 AG-9）。口令不出现在任何代码路径或输出中。

## 测试命令与结果

新增用例：

```
...\python.exe -m pytest agent/tests/test_remote_probe.py agent/tests/test_task_status_aborted.py -q
```
→ `10 passed in 0.56s`

先看到失败（RED）：`RemoteTaskProbe` 导入失败；`status_label` 缺失；`run_and_wait` 返回超时信封
（`out.get("status") is None`）。

全量回归：

```
...\python.exe -m pytest agent/tests -q
```
→ `365 passed, 237 subtests passed in 39.03s`（此前为 355 passed，本任务净增 10 条，无回归）

## 关注点 / 备注

- 本任务按指示**未**把 `RemoteTaskProbe` 接进 `http_api` / `Runtime`；真实启用由 AG-9 完成。
- `explain.status_label` 是本次为可测试性抽出的新公开函数（`__all__` 已登记），`explain_status`
  输出与既有格式一致，仅补了 `aborted` 一态。
- `LongTaskBackend` 现在依赖 `manager.creates_task`；`LongTaskManager` 已提供该方法，测试脚手架
  （`FakeToolBackend` + 真实 `LongTaskManager`）验证通过。
