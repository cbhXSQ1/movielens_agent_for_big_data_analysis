# 迭代二（机器学习）Agent + 前端 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把端侧的迭代二机器学习能力（分类 / 聚类 / 降维）接入 Agent 工具表与前端，并打通"结果板块 + 视图 + 模型产物下载"。

**Architecture:** 端侧新增 6 项 MCP 能力（`train_classifier` / `cluster_users` / `reduce_dimensions` / `predict_high_rating` / `list_ml_models` / `get_ml_model`）。Agent 侧按现有"远端能力服务 + 工具表 + 长任务 + 下载中转"的模式接入；前端按 `task_type` 渲染三类结果视图，模型产物按 `role` 分组、按 `kind` 决定是否可预览，二进制模型经**模型下载路由**由 Agent 流式中转。

**Tech Stack:** Python 标准库 + 现有 httpx；Vue 3 + Vite + TS + Naive UI + Pinia + Vitest。

## Global Constraints（契约以 `docs/hadoop/迭代二_机器学习_数据契约.md` 为准）

- **契约版本 1.0**，只做加性变更。
- **6 项能力**：`train_classifier(algorithm∈{naive_bayes,random_forest})`、`cluster_users(algorithm∈{kmeans,hierarchical},k?)`、`reduce_dimensions(algorithm∈{pca,nmf},n_components?)`、`predict_high_rating(model_id,user_id,movie_id)`、`list_ml_models()`、`get_ml_model(model_id)`；训练类均带 `data_version?`、`tag?`、`params?`。
- **`params` 白名单**：未知键 → `USAGE`；`k`∈[2,50]（两聚类共用）；`n_components`∈[2,20]。
- **模型编号 = 任务编号**（`ml-YYYYMMDD-HHMMSS-xxxx`）；训练与清洗**共用执行位**（训练期间发清洗被拒并给占用者）。
- **指标 0–1 小数、四位**；前端显示百分比自行 ×100。
- **产物条目**：`kind`=文件格式（`joblib`/`json`/`csv`/`jsonl`/`md`/`other`），`role`=用途（`model`/`profile`/`labels`/`coords`/`meta`）。
- **模型下载路由**：`GET /download/model/{model_id}/{path}`（同一口令、Host 白名单、流式）；失败回 JSON 信封。**产物地址 = 该路由 + `get_ml_model` 的 `artifacts[].path`**。
- 新增错误码 `MODEL_NOT_FOUND`（加性）。
- 口令绝不写入日志/信封；用户可见文案不含「冒烟/对账/口径」。
- 单机训练、不提交集群作业；"作业聚合"为端侧配置、对端零改动。

## 文件结构

**Agent 侧**
- Modify `agent/mcp_tools.py`：新增 6 项 ML 工具规格。
- Modify `agent/tool_policy.py`：新增 6 项策略条目（键集与 `TOOL_SPECS` 对齐）。
- Modify `agent/mcp_contract.py`：新增 `MODEL_NOT_FOUND` + 说明。
- Modify `agent/runtime/long_task.py`：训练工具纳入"创建任务"集合。
- Modify `agent/runtime/long_task.py` `result_payload` + `agent/runtime/offline.py`：按 `task_type` 透传 ML 结果板块。
- Create `agent/ml_models.py`：模型列表/详情（`list_ml_models`/`get_ml_model` 的薄封装 + 产物地址拼装）。
- Modify `agent/mcp_download.py`：新增 `open_model_stream(...)`。
- Modify `agent/http_api.py`：`/api/models`、`/api/models/{id}`、`/api/models/download`（中转）。
- 测试：`agent/tests/test_ml_tools.py`、`test_ml_result_blocks.py`、`test_ml_download.py`、`test_models_api.py` + 既有计数用例。

**前端**
- Create `frontend/src/components/results/ml/`：`ClassificationCard.vue`、`ClusteringPanel.vue`、`DimReductionPanel.vue`。
- Modify `frontend/src/composables/useResultBlocks.ts`、`frontend/src/api/types.ts`、`frontend/src/api/resources.ts`（或新 `models.ts`）、`frontend/src/api/http.ts`、`frontend/src/views/panels/ResourcesPanel.vue`（模型区）、结果视图组件。
- 测试：`frontend/src/__tests__/ml.*.test.ts`。

---

### Task AG-ML-1: 工具表新增 6 项 ML 工具

**Files:** `agent/mcp_tools.py`、`agent/tool_policy.py`、`agent/tests/test_ml_tools.py`、`agent/tests/test_mcp_tools.py`（计数 24→30）

**Interfaces — Produces:** `mcp_tools.REMOTE_TOOL_SPECS` 含 6 项新工具，参数逐字对齐数据契约 §1；`tool_policy.POLICY` 键集与 `TOOL_SPECS` 一致。

- [ ] 写失败测试：断言 6 个工具名存在、`source=="mcp"`、训练类 `long_running is True`、`algorithm` 枚举值正确、`k`(minimum 2,maximum 50)、`n_components`(2..20)、`params` 为 object。
- [ ] 跑失败 → 在 `mcp_tools.py` 加 6 项规格、`tool_policy.py` 加 6 条 `{}` → 更新计数断言（`test_mcp_tools.py` 30）→ 跑通过。
- [ ] 提交：`feat(agent): 工具表新增迭代二 6 项 ML 能力`

### Task AG-ML-2: 结果板块按 task_type 透传

**Files:** `agent/runtime/long_task.py`（`result_payload`）、`agent/runtime/offline.py`、`agent/tests/test_ml_result_blocks.py`

**Interfaces:** 成功结果的 `metrics` 按 `task_type` 落到 `blocks`：`classification`→`classification`、`clustering`→`clustering`、`dimensionality_reduction`→`dimensionality_reduction`（键名与前端约定一致，取值为远端 `metrics` 原样）；`artifacts`、`params`、`time_boundaries` 已在既有映射中，确认包含。

- [ ] 假客户端返回带 `task_type`/`metrics` 的结果 → 断言对应 block 存在且值原样。
- [ ] 实现映射（复用既有"present-only"风格，不造空壳）→ 跑通过 → 提交：`feat(agent): 结果板块透传三类 ML 指标`

### Task AG-ML-3: 训练工具纳入长任务集合

**Files:** `agent/runtime/long_task.py`、`agent/tests/`（既有长任务用例）

**Interfaces:** `TOOL_TASK_NAMES`/`creates_task` 纳入 `train_classifier`/`cluster_users`/`reduce_dimensions`；确认 `model_id`（=远端 `task_id`）作为 `hadoop_task_id` 进入记录。

- [ ] 测试：调用训练工具成功后被登记为长任务；`model_id` 与 `hadoop_task_id` 同值。
- [ ] 实现 → 跑通过 → 提交：`feat(agent): 训练工具接入长任务（model_id=hadoop_task_id）`

### Task AG-ML-4: 模型下载中转

**Files:** `agent/mcp_download.py`、`agent/http_api.py`、`agent/tests/test_ml_download.py`

**Interfaces — Produces:** `mcp_download.open_model_stream(config, model_id, relpath, transport=None) -> (iterator, headers, error)`；http_api `GET /api/models/download?model_id=&path=` 流式中转（带口令），错误码与状态映射同既有下载。

- [ ] 测试（`httpx.MockTransport`）：成功流式返回字节 + 透传 `Content-Disposition`；`FILE_NOT_FOUND`/`FORBIDDEN_PATH` 原样透传；路径越界本地拒绝（`..`/绝对/反斜杠）不发请求。
- [ ] 实现：URL = `config` 基址 + `/download/model/{quote(model_id)}/{quote(relpath, safe='/')}`；沿用既有 relpath 守卫与错误解析 → 跑通过 → 提交：`feat(agent): 模型产物经模型下载路由流式中转`

### Task AG-ML-5: 模型列表 / 详情

**Files:** Create `agent/ml_models.py`、Modify `agent/http_api.py`、`agent/tests/test_models_api.py`

**Interfaces:** `ml_models.list_models(client)` → `{"models":[...]}`；`ml_models.get_model(client, model_id)` → `{"model":{...}}`；产物地址由 `model_id`+`artifacts[].path` 拼成 `/api/models/download?model_id=&path=`。http_api：`GET /api/models`、`GET /api/models/{model_id}`。

- [ ] 测试：假客户端 → 两个端点返回远端结果；产物地址拼装正确。
- [ ] 实现 → 跑通过 → 提交：`feat(agent): 模型列表/详情端点`

### Task AG-ML-6: 错误码 MODEL_NOT_FOUND

**Files:** `agent/mcp_contract.py`、`agent/tests/test_mcp_errors.py`

- [ ] 测试：`MODEL_NOT_FOUND` 在 `STRUCTURED_ERROR_CODES`，`describe_error` 给中文。
- [ ] 实现 → 跑通过 → 提交：`feat(agent): 新增 MODEL_NOT_FOUND`

### Task AG-ML-7: Agent 收口

- [ ] 跑全量：`python -m pytest agent/tests -q` 全绿；补一条"训练→查状态→终态取结果（假客户端）"端到端用例。
- [ ] 提交：`test(agent): 迭代二接入收口`

---

### Task FE-ML-1: 结果三视图（按 task_type）

**Files:** `frontend/src/components/results/ml/*.vue`、`frontend/src/composables/useResultBlocks.ts`、`frontend/src/api/types.ts`、结果视图宿主组件、测试

**Interfaces:** `useResultBlocks` 新增 `classification`/`clustering`/`dimensionalityReduction`（取 `blocks` 原值）；宿主按 `task_type` 分发到三组件。

- [ ] 分类卡：F1/AUC/PR-AUC（**×100 显示**）、P/R、混淆矩阵、特征重要性（附 `feature_importance_note`）、`split_sizes`、`leakage` 小字。
- [ ] 聚类面板：每群 `label`/`size`/`top_genres`/`behavior`；未传 `k` 时 `k_selection` 折线。
- [ ] 降维面板：`comparison`（before/after/agreement）+ `explained_variance_ratio`/`reconstruction_error`/`resource` + 坐标散点（从 `role=coords` 产物取数，可经下载/预览接口）。
- [ ] TDD：给定三份样例结果，断言渲染的字段与数值；提交：`feat(frontend): 迭代二结果三视图`

### Task FE-ML-2: 资源页模型区（列表/详情/下载）

**Files:** `frontend/src/api/models.ts`（或并入 resources）、`frontend/src/stores`（模型）、`ResourcesPanel` 新增"模型"段、测试

- [ ] 列模型（`/api/models`）；详情（`/api/models/{id}`）按 **`role` 分组**展示产物；按 **`kind`** 判定：`json/csv/jsonl/md` 可预览，`joblib` **只下载**（提示"不能在线预览"）；下载走 `/api/models/download`。
- [ ] TDD + `npm run build`；提交：`feat(frontend): 模型库（列表/详情/下载）`

### Task FE-ML-3: 单位与错误码

**Files:** `frontend/src/api/http.ts`、相关组件

- [ ] 新增 `MODEL_NOT_FOUND` 中文；百分比换算统一（0–1 → ×100）；提交：`feat(frontend): 迭代二单位换算与错误码`

### Task FE-ML-4: 工具表/离线表单适配

**Files:** 离线表单组件、`stores/tools.ts`

- [ ] 训练类工具的 `algorithm`/`k`/`n_components`/`params` 在离线表单里可选；`params` 用键值编辑（未知键前端先拦）。提交：`feat(frontend): 离线表单支持 ML 参数`

### Task FE-ML-5: 前端收口

- [ ] `npm run test` 与 `npm run build` 全绿；对新组件补关键用例；提交：`test(frontend): 迭代二收口`

---

## 联调（端侧实现就绪后）

- 用真数据各跑一次分类/聚类/降维：`train_classifier`/`cluster_users`/`reduce_dimensions` → 查状态 → 终态取结果 → 前端三视图正确；`predict_high_rating` 单条预测；模型列表/详情/下载经模型下载路由可用；双编号（`agent_task_id` ↔ `model_id`）正确。

## Self-Review

- **契约覆盖**：6 能力/参数/白名单 → AG-ML-1；结果字段 → AG-ML-2 + FE-ML-1；下载路由 → AG-ML-4 + FE-ML-2；模型编号=任务编号 → AG-ML-3；指标单位 → FE-ML-3；错误码 → AG-ML-6 + FE-ML-3。
- **占位符扫描**：前端组件步骤引用了数据契约的字段清单（§3.2–§3.5），执行者照契约实现，不留空。
- **类型一致**：`kind`/`role`、`task_type`、`model_id` 贯穿 AG-ML-2/4/5 与 FE-ML-1/2。
- **依赖**：端侧 ML 未实现前，全部用**假客户端**联测；真数据联调在端侧就绪后进行。
