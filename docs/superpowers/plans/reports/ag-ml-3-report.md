# AG-ML-3 报告：训练工具纳入长任务集合

**状态：** 完成
**范围：** `agent/runtime/long_task.py`、`agent/tests/test_ml_long_task.py`

## 改动

- `TOOL_TASK_NAMES` 新增 `train_classifier` / `cluster_users` / `reduce_dimensions`，
  使 `creates_task` 对三类训练工具返回 True，`LongTaskBackend` 在受理成功（`ok=True`）
  后即登记长任务（异步受理 + 轮询），与 `start_cleaning_task` 等完全同路。
- 未新增 id 字段：`new_record` 沿用既有 `hadoop_task_id = envelope["task_id"]`。
  按数据契约 §0/§3.1「模型编号 = 任务编号」，ML 的执行侧 `task_id` 即 `model_id`，
  只以单一 `hadoop_task_id` 入库。
- `is_long_running` 展示语义未改（回退映射 `LONG_RUNNING_TOOLS` 未动；训练类工具本就由
  工具规格 `long_running=True` 决定展示，UG 不重复声明）。

## 测试（TDD）

`agent/tests/test_ml_long_task.py`（假工具后端 + 本地 Runtime，无网络）：

- `test_training_tools_are_task_creators`：三训练工具 `creates_task` 为真。
- `test_read_tools_are_not_task_creators`：读类工具为假。
- `test_training_registers_and_hadoop_id_is_envelope_task_id`：经 `LongTaskBackend`
  调用训练工具后登记 1 条记录，`hadoop_task_id == envelope["task_id"]`。
- `test_read_tool_does_not_register`：读工具不登记。

先见证失败（6 项训练相关断言失败），再最小实现转绿。

## 验证

- 目标用例：4 passed, 14 subtests passed。
- 全量：`python -m pytest agent/tests -q` → **480 passed, 353 subtests passed**（原 476）。

## 顾虑

- 无。行文案未含受限词。
