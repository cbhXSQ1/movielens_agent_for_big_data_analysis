# -*- coding: utf-8 -*-
"""ml —— 迭代二的机器学习核心（分类 / 聚类 / 降维）。

分层约定：
  * 本包**不依赖** `mcpserver`（服务层），只做"算"与"存"：取数、构造特征、训练、评估、落盘、登记；
  * 服务层（`hadoop/mcpserver/`）负责受理、任务状态、取消、并发与对外信封；
  * 本包抛 `TrainingError`（带结构化码），由服务层映射成统一的失败信封。

入口只有 `api` 里的四个：训练、预测、列模型、取模型 —— 对外能力的增删不改本包其余部分。
"""

from .api import (  # noqa: F401
    TASK_CLASSIFICATION,
    TASK_CLUSTERING,
    TASK_REDUCTION,
    TASK_TYPES,
    ALGORITHMS,
    ML_ROOT_REL,
    TrainingError,
    run_training,
    effective_params,
    predict_rating,
    list_models,
    get_model,
    model_dir,
    model_exists,
)
