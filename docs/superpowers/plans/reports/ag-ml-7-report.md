# AG-ML-7 报告：Agent 侧收口（迭代二 ML）

- 日期：2026-10-18
- 任务：`docs/superpowers/plans/2026-10-18-iteration2-ml-agent-frontend.md` → Task AG-ML-7
- 范围：Agent 侧收口（不重写他人代码）

## 1. 全量本地测试（绿）

命令（仓库根 `E:\学\大数据分析\movielens_agent_for_big_data_analysis`）：

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q
```

改动前基线输出：

```
492 passed, 353 subtests passed in 60.84s (0:01:00)
```

新增用例单跑：

```
& "...\.venv\Scripts\python.exe" -m pytest agent/tests/test_ml_e2e.py -q
→ 1 passed in 0.45s
```

改动后全量输出：

```
........................................................................ [ 14%]
..................................................................................................................... [ 38%]
............................................................................................... [ 57%]
........................................................................................... [ 76%]
........................................................................ [ 90%]
..............................................                   [100%]
493 passed, 353 subtests passed in 61.98s (0:01:01)
```

## 2. 迭代二各项一致性核查（grep/verify，不改他人代码）

核查脚本（导入真实模块，不触网）：

```python
from agent import mcp_tools, tool_policy
names = ['train_classifier','cluster_users','reduce_dimensions',
         'predict_high_rating','list_ml_models','get_ml_model']
spec = [s['name'] for s in mcp_tools.REMOTE_TOOL_SPECS]
```

结论：

- ✅ 6 项 ML 工具全部在 `mcp_tools.REMOTE_TOOL_SPECS`（远端工具共 30 项，24→30 符合 AG-ML-1）。
- ✅ `tool_policy.POLICY` 键集 = 30 项远端 + 1 项本机专属 `quick_clean_demo` = 31；`REMOTE_TOOL_SPECS \ POLICY` 为空集，策略与工具表对齐（`quick_clean_demo` 属本机专属，预期额外）。
- ✅ 训练工具为任务创建类：`agent/runtime/long_task.py:65-67` 的 `TOOL_TASK_NAMES` 含 `train_classifier`/`cluster_users`/`reduce_dimensions`，`creates_task()` 据此判定为 True。
- ✅ `result_payload` 按 `task_type` 透传三类板块：`long_task.py:307-312`，`ML_TASK_TYPES = ("classification","clustering","dimensionality_reduction")`，带 `metrics` 时 `blocks[task_type] = metrics` 原样（present-only，不造空壳）；`params`/`artifacts`/`time_boundaries` 在既有映射中（`long_task.py:294-300`）。`offline.result_payload` 复用同一映射（`offline.py:180-199`）。
- ✅ `open_model_stream` 存在：`agent/mcp_download.py:213`。
- ✅ 三个端点存在：`agent/http_api.py` `/api/models/download`（:1016-1021）、`/api/models`（:1023-1024）、`/api/models/{model_id}`（:1027-1028）；模型产物地址拼装 `agent/ml_models.py:52`。
- ✅ 错误码 `MODEL_NOT_FOUND` 已加性登记：`mcp_contract.py:87/116/224`。

## 3. 新增端到端本地用例（AG-ML-7）

文件：`agent/tests/test_ml_e2e.py`（154 行，`unittest`，无网络）

- 顶部显式声明假客户端 `FakeRemoteClient`（`mcp_contract.McpClient` 子类），受控返回「训练 / 查状态 / 取结果」三类罐头信封；终态结果信封含 `task_id` / `task_type` / `metrics` / `artifacts`。
- 链路（经运行时工具后端 + `RemoteTaskProbe`，不触网）：
  1. `run_offline(cid, "train_classifier", {"algorithm":"random_forest"})` → 断言经同一执行后端登记长任务，`hadoop_task_id == 客户端 task_id == ml-20261018-120000-abcd`（模型编号 = 任务编号）。
  2. 假探针驱动终态：改写客户端 `status=succeeded` 后 `poller.tick()` → 记录进 `succeeded`，`get_task_result` 按 `hadoop_task_id` 取值。
  3. `result_payload` 的 `blocks` 含 `classification`，且 `blocks["classification"] == CLASSIFICATION_METRICS`（远端 `metrics` 原样）；同时校验 `params`、`artifacts` 板块。
- 复用既有模式：`provider_factory=_no_model` + `task_poll_interval=3600`，终态走确定性收尾，全程不连网、不触模型。

## 4. 提交

```
7da5b67 test(agent): 迭代二接入收口（假客户端端到端）
 1 file changed, 154 insertions(+)
 create mode 100644 agent/tests/test_ml_e2e.py
```

仅提交自有新测试文件；仓内他人未暂存改动（前端测试/组件等）保持原样，未触碰。

## 5. 发现与关注点

- 无阻断项。基线 492 → 493（+1 新用例），无回归。
- `tool_policy.POLICY` 比远端工具表多一项本机专属 `quick_clean_demo`（设计如此，非不一致）。
- 未执行任何破坏性 git 操作（reset/amend/rebase 等），未提交他人文件。
