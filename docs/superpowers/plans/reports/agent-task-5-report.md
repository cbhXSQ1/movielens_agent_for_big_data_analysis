# Agent Task 5 报告 —— 扩充结构化错误码与细类

- 计划：`docs/superpowers/plans/2026-10-18-agent-remote-mcp.md` → 「### Task 5: 扩充结构化错误码与细类」
- 状态：DONE
- 日期：2026-10-18

## 目标（本任务）

远端能力服务会产生一批 Agent 契约集尚未枚举的结构化错误码。本任务**加性**扩充
Agent 侧的错误码词汇表，并补上 `reason_kind` 细类常量；同时提供统一的
`describe_error(error)` 出口文案函数。**不改变**既有映射语义。

## 接口契约（`agent/mcp_contract.py`）

### 新增错误码常量并入 `STRUCTURED_ERROR_CODES`

新增 11 个码（保留原有 12 个，共 23 个）：

`ABORTED`、`FILE_NOT_FOUND`、`FORBIDDEN_PATH`、`CONTENT_TOO_LARGE`、
`PRODUCT_INVALID`、`CANCEL_FAILED`、`UNAUTHORIZED`、`FORBIDDEN_HOST`、
`WAIT_LIMIT_REACHED`、`INTERNAL_ERROR`、`NOT_PREVIEWABLE`。

命名与 `hadoop/mcpserver/contract.py` 的既有名称逐字对齐，两端不各造一套。
`NOT_PREVIEWABLE` 在远端属「保留码」（当前以「成功 + 不可预览」表达，不以错误形式出现），
本端仍收录进词汇表，以便该码出现时能给出可读说明而非裸露原码。

### `REASON_KINDS`

```python
REASON_KINDS = ("control", "sidecar", "residue", "directory", "not_regular_file")
```

与远端读取层 `hadoop/mcpserver/readers.py::REASON_KINDS` **同一套取值**：前三个
（control/sidecar/residue）对应「不是任务产物」，后两个（directory/not_regular_file）
对应「不是一份文件」。

### `describe_error(error) -> str`

把一个错误（dict 或码字符串）翻成面向用户的**可读中文**。回退顺序（前者命中即返回）：

1. 已知结构化码 / driver 自产码（`CONFIG_INVALID`）→ 本端固定中文说明；
2. 远端信封里的 `error.message`（原样转述，不改写成别的结论）；
3. 原始码（未知码也不丢，便于排障）；
4. 兜底说明 `UNKNOWN_ERROR_MESSAGE`。

固定说明集中在 `ERROR_MESSAGES`；driver 自产码说明在 `_DRIVER_NATIVE_MESSAGES`。
**文案红线**：描述场景，不含「冒烟」「对账」「口径」等词（有测试锁定）。

## 改动的文件

| 文件 | 变更 |
| --- | --- |
| `agent/mcp_contract.py` | 新增 11 个 `CODE_*` 常量；扩充 `STRUCTURED_ERROR_CODES`；新增 `REASON_KINDS`；新增 `ERROR_MESSAGES` / `_DRIVER_NATIVE_MESSAGES` / `UNKNOWN_ERROR_MESSAGE` 与 `describe_error()` |
| `agent/tests/test_mcp_errors.py` | 新增（13 条用例 / 51 subtests）：新码存在、既有码保留、`REASON_KINDS` 精确集合、`describe_error` 回退顺序与文案安全、`normalize_error` 语义无损 |

> 严格限定在任务给定的「Only touch」清单内：生产代码只碰 `mcp_contract.py`，
> 测试只新增 `tests/test_mcp_errors.py`；未改动任何既有测试。

## 关键实现点

- **TDD**：先写失败用例（13 条新用例中 34 个断言点全红：`AttributeError: describe_error` /
  `REASON_KINDS`、新码不存在的 `AssertionError`），再实现，再转绿。
- **语义无损**：`normalize_error` **未改动一行**。副作用只是原先落入「未知码」分支的
  这批码，现在命中「已是结构化码 → 原样保留」分支 —— 对远端信封（带结构化码、不带
  driver 退出码）而言结果完全一致；既有退出码映射表（`EXIT_CODE_MAP`）与传输层映射
  一字未动，既有用例 `test_stage8_mcp_seam.py` 全绿。
- **文案单一来源**：所有出口中文说明集中于 `ERROR_MESSAGES`，避免各处各写一套。
- **不引入依赖**、不连网：纯常量与纯函数，可在裸解释器里单测。

## 测试命令与结果

定向：

```
& "E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv\Scripts\python.exe" -m pytest agent/tests/test_mcp_errors.py -q
```

结果：`13 passed, 51 subtests passed in 0.06s`

全量（无网络、不连远端服务）：

```
& "...\python.exe" -m pytest agent/tests -q
```

结果：`355 passed, 237 subtests passed in 38.94s`
（基线 `342 passed, 186 subtests`；净增 13 条用例 / 51 subtests，无回归）

## 关注点 / 备注

1. **`NOT_PREVIEWABLE` 的位置差异**：远端把它放在 `RESERVED_ERROR_CODES`（不在
   `STRUCTURED_ERROR_CODES`），本任务按计划把它并入本端 `STRUCTURED_ERROR_CODES`
   以便 `describe_error` 覆盖。这是**加性**收录，不影响远端「成功 + 不可预览」的既有
   表达方式；若后续要求两端集合严格同构，可再讨论。
2. **`describe_error` 对已知码优先本端说明**：已知码即使带 `message` 也返回固定中文
   （按计划「known code → ...（fall back to message, then raw code）」）。若业务上希望
   远端 `message` 优先于本端说明，只需调整函数内两段判断的先后，改动极小。
3. **`normalize_error` 的细微归类变化**：新码从「未知码」变为「已知结构化码」。理论上
   若某个已识别的新码同时又带 driver 退出码，将不再按退出码换算 —— 但远端信封不带
   driver 退出码，此路径在当前流程中不会出现；既有映射表与既有用例均无变化。
4. 口令红线：本任务不新增任何日志输出，也不接触口令。
