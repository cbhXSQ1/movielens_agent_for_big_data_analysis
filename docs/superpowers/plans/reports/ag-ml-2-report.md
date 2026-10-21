# AG-ML-2 报告：结果板块按 task_type 透传

## 状态
完成。测试先行（RED→GREEN），全量套件绿。

## 改动
- `agent/runtime/long_task.py`
  - 新增常量 `ML_TASK_TYPES = ("classification", "clustering", "dimensionality_reduction")`。
  - `result_payload`：当结果信封的 `task_type` 命中上述三类且带 `metrics` 时，
    把远端 `metrics` 原样放入 `blocks[task_type]`（`blocks["classification"]` /
    `blocks["clustering"]` / `blocks["dimensionality_reduction"]`）。
  - 新增 `params` / `artifacts` 两个透传板块（present-only）；`time_boundaries`
    既有映射已覆盖，确认无需改动。
  - 既有板块键（`task_status` / `score_comparison` / `data_volume` /
    `quarantine_samples` / `report` / `limitations`）与 ML/评分键名一律不变。
- `agent/runtime/offline.py`：**未改动**。其 `result_payload` 直接委托
  `long_task.result_payload`，同一映射自动覆盖，无重复实现。
- `agent/tests/test_ml_result_blocks.py`（新增）：三类 `task_type` 的块落位与
  取值原样、metrics 缺席/未知 `task_type` 不造块、params/artifacts 透传与缺席、
  既有键不变、离线直调走同一映射。

## 测试
`python -m pytest agent/tests -q` → **460 passed, 339 subtests passed**（新增 9 例）。
单文件：`agent/tests/test_ml_result_blocks.py` → 9 passed, 6 subtests passed。

## 约束遵循
- 全程无网络（纯 dict 假数据）。
- 未触碰其它文件；既有板块键与键名零改动（纯加性）。
- 文案不含「冒烟/对账/口径」。

## 关注点
- `params` / `artifacts` 板块为新增透传键：与运行时既有的"产物收集"（`artifact_added`
  事件 / 对话上下文 `artifacts`）是两套并行通道，前者是结果信封内的一次性展示数据，
  互不影响。
