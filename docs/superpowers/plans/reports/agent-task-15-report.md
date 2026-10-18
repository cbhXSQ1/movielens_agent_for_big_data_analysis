# Task 15 报告：结果板块透传「版本与时间边界」

## 状态
完成（本地全绿；未推送）。提交 `e5ff733fdc9018ab93bb45345007a6ff95e916d0`
（`feat(agent): 结果板块透传版本与时间边界（缺失不造）`），仅含 Agent 侧文件。

## 目标
远端 `get_task_result` 返回 `versions`（四类）/ `version_hashes` / `versions_source`
/ `time_boundaries` {T1,T2}，但 Agent 的 `result_payload` 只把部分字段放进 `blocks`，
前端拿不到「版本与时间边界」（接口文档 §9.2 要求直接展示、不推断）。本次把
`versions`、`time_boundaries`（另附出处 `versions_source`）透传进结果板块。

## 改动
`agent/runtime/long_task.py` → `result_payload(record, result)` 的 `blocks` 映射追加三项：

```python
# 版本与时间边界（接口文档 §9.2）：执行侧给什么就透传什么，
# 缺席就不建板块 —— 不推算、不拿空壳顶替。
("versions", ("versions",)),
("versions_source", ("versions_source",)),
("time_boundaries", ("time_boundaries",)),
```

机制说明：既有映射采用「键在信封里才建板块」（`if key in result`），因此
**信封缺席时不建块、不给 null、不给空 dict**，天然满足「不编造」。

既有板块键 `task_status` / `score_comparison` / `data_volume` /
`quarantine_samples` / `report` / `limitations` 全部不变，为纯加性改动。

### 两条结果构造路都覆盖
- `agent/runtime/long_task.py::result_payload` —— 本次直接改动（长任务完成路径）。
- `agent/runtime/offline.py::result_payload` —— **委派**长任务的同一映射
  （其末尾 `return long_task.result_payload(record, envelope)`，docstring 亦注明
  「与长任务同一映射」）。故未在 `offline.py` 重复一份映射（避免两处漂移），
  离线直调路径经共享映射同样透传；已由用例直接对 `offline.result_payload` 断言。

### 未纳入的字段
`version_hashes` 未单独建块：§9.2 要求展示的是「四类版本 + 两个时间边界」，
摘要不在展示范围，按「最小、诚实」原则未透传（信封里存在也不会被臆造）。

## 测试（TDD；假客户端 / 定值结果，无网络）
先写失败用例 `agent/tests/test_version_blocks.py`（5 项），确认
`KeyError: 'versions'` 的**预期失败**后再实现，随即使其转绿：

```
& "...\python.exe" -m pytest agent/tests/test_version_blocks.py -q
# 实现前：2 failed, 3 passed（KeyError: 'versions'）
# 实现后：5 passed
```

覆盖：
1. 长任务结果含 `versions` + `time_boundaries` + `versions_source` → 板块**原值**透传；
2. 长任务结果缺席这些字段 → 板块**不出现**（无 null / 空 dict 造壳）；
3. 既有板块键一个不少（加性回归）；
4. 离线直调信封同一断言（原值透传）；
5. 离线直调信封缺席 → 不出现。

## 最终验证（精确命令与结果）
```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests -q
403 passed, 240 subtests passed in 44.00s
```
基线 398 passed；净增 5 项（新文件 5 个用例），subtests 不变。

## 关切 / 备注
- 无网络：用例仅调纯函数与定值信封，未触远端、未构造模型。
- `offline.py` 未改动：离线路径经共享映射自动获得同一行为，避免重复映射漂移。
- 报告文件 `docs/superpowers/plans/reports/agent-task-15-report.md` 未纳入提交
  （本次提交仅 Agent 代码与用例）。
- 文案未使用受限词（冒烟 / 对账 / 口径）。
