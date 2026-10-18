# ag-ml-1 报告：工具表新增迭代二 6 项 ML 能力

## 任务
对齐 `docs/hadoop/迭代二_机器学习_数据契约.md` §1，在远端工具表新增 6 项 ML 工具，
并同步策略表键集、更新工具计数锚点。参数形状以数据契约 §1 / §1.3 为准。

## 改动（仅五个文件）
- `agent/mcp_tools.py`
  - 新增参数类型 `PARAM_OBJECT = "object"`，并纳入 `_JSON_TYPE`（→ JSON Schema `object`）。
  - 新增复用参数说明：`ML_DATA_VERSION`、`CLASSIFIER_PARAMS`、`CLUSTER_PARAMS`、
    `REDUCE_PARAMS`（`params` 白名单键与含义照 §1.3，说明未知键会被拒绝）。
  - `REMOTE_TOOL_SPECS` 追加 6 项（source 均为 `mcp`）：
    | 工具 | long_running | estimated_seconds | 关键参数 |
    |---|---|---|---|
    | `train_classifier` | True | 600 | `algorithm` 必填枚举 `naive_bayes/random_forest`；`data_version`、`tag`、`params`(object) |
    | `cluster_users` | True | 300 | `algorithm` 必填枚举 `kmeans/hierarchical`；`k` int 2～50；`data_version`、`tag`、`params` |
    | `reduce_dimensions` | True | 300 | `algorithm` 必填枚举 `pca/nmf`；`n_components` int 2～20；`data_version`、`tag`、`params` |
    | `predict_high_rating` | False | 3 | `model_id`、`user_id`、`movie_id` 均必填 |
    | `list_ml_models` | False | 1 | 无参数 |
    | `get_ml_model` | False | 1 | `model_id` string 必填 |
  - 段首注释「24 项」→「30 项」。
- `agent/tool_policy.py`
  - `POLICY` 追加 6 条 `"<tool>": {}`（键集与 `TOOL_SPECS` 保持一一对应）。
- `agent/tests/test_ml_tools.py`（新增）
  - 断言 6 名存在、`source=="mcp"`、训练类 `long_running is True`、三组 `algorithm` 枚举、
    `k` 2/50、`n_components` 2/20、`params` 为 `object`、各工具 required 形状。
- `agent/tests/test_mcp_tools.py`
  - 计数锚点 24 → 30（`REMOTE_TOOL_SPECS` 与 `remote_tool_names()` 两处 + 文档字符串）。
- `agent/tests/test_tool_specs.py`
  - 计数锚点 25 → 31（`len(tool_names())` = 30 远端 + `quick_clean_demo`；注释同步 24→30）。

## 参数形状说明
- `params` 采用 `{"type": "object", "description": "…白名单键与含义…"}`，逐工具按算法族列出
  §1.3 可用键；不在表内的键由远端拒绝（`USAGE`）。
- `user_id` / `movie_id`：契约 §1.2 记为「string 或 int」，§7 定为「字符串进、字符串出」，
  故本表按 `string` 声明（与 §3.6 输出类型一致）。

## TDD 证据
- RED：先写 `test_ml_tools.py` 并运行 → **38 failed, 8 passed**（因 6 项工具不存在）。
- GREEN：实现后定向 6 个测试文件 → **83 passed, 278 subtests passed**。

## 测试
- 定向：`test_ml_tools.py` + 计数/策略相关 5 文件 → 全绿。
- 全量：`python -m pytest agent/tests -q` → **451 passed, 333 subtests passed**。
  - 注：本次开工时基线为 437 passed + 2 failed（`test_mcp_errors.py` 的 `MODEL_NOT_FOUND`，
    属并发任务 AG-ML-6）；运行到收尾时该并发改动已落盘，故全量转绿。

## 约束遵守
- 未联网；未触碰「冒烟 / 对账 / 口径」。
- 代码文件仅改 `agent/mcp_tools.py`、`agent/tool_policy.py`、`agent/tests/test_ml_tools.py`、
  `agent/tests/test_mcp_tools.py`；另因「修掉其它计数锚点」的显式要求，改了
  `agent/tests/test_tool_specs.py` 的 `len(tool_names())` 断言（25→31）。

## 遗留 / 关注点
- `agent/runtime/tool_exec.py` 文档字符串仍写「（24 项）」，属注释、不影响测试与行为；
  不在本任务允许改动文件内，未改。
- 训练类工具的「长任务集合 / 执行位」语义由后续 AG-ML-3 接入；本任务的策略条目暂为 `{}`。
- `stream.LONG_RUNNING_TOOLS` 兜底映射未纳入 3 个训练工具（规格已带 `long_running`，
  运行时以规格为准即可），如需一致由后续任务处理。
