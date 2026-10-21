# ag-ml-6 报告：新增结构化错误码 `MODEL_NOT_FOUND`

## 任务
对齐 `docs/hadoop/迭代二_机器学习_数据契约.md` §5，加性新增结构化错误码
`MODEL_NOT_FOUND`，并入 `STRUCTURED_ERROR_CODES`，并在 `describe_error` 中给出可读中文说明。

## 改动（仅两文件）
- `agent/mcp_contract.py`
  - 新增常量 `CODE_MODEL_NOT_FOUND = "MODEL_NOT_FOUND"`（附注说明与
    `TASK_NOT_FOUND` / `FILE_NOT_FOUND` 的区分）。
  - `STRUCTURED_ERROR_CODES` 末尾加性加入 `CODE_MODEL_NOT_FOUND`。
  - `ERROR_MESSAGES` 增加：`CODE_MODEL_NOT_FOUND: "模型编号不存在：请用列模型确认编号。"`
- `agent/tests/test_mcp_errors.py`
  - `NEW_CODES` 加入 `"MODEL_NOT_FOUND"`（覆盖「在 `STRUCTURED_ERROR_CODES` 中」断言）。
  - 新增 `test_model_not_found_has_readable_chinese`：`describe_error` 返回含「模型」的中文，
    且不回退到远端 `message` 或原始码。

## TDD 证据
- RED：先加测试，2 项失败（`MODEL_NOT_FOUND` 不在码集；`describe_error` 回退到 `raw-en-body`）。
- GREEN：实现后 `agent/tests/test_mcp_errors.py` 全通过。

## 测试
- 定向：`pytest agent/tests/test_mcp_errors.py -q` → 全绿。
- 全量：`pytest agent/tests -q` → **438 passed, 258 subtests passed**（此前 437，+1 为新测试）。

## 文案合规
说明文案为「模型编号不存在：请用列模型确认编号。」，不含「冒烟 / 对账 / 口径」；
测试 `test_all_known_codes_are_readable_and_wording_safe` 亦覆盖该红线。

## 约束遵守
- 只改 `agent/mcp_contract.py` 与 `agent/tests/test_mcp_errors.py`。
- 未联网；加性变更（不改既有码/映射/回退顺序）。

## 遗留/关注点
- 无功能隐患；纯加性扩充。
