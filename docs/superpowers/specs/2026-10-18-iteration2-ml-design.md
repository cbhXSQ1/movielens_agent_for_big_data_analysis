# 迭代二（机器学习：分类 / 聚类 / 降维）设计 —— Agent + 前端

> **契约以端侧为准**：`docs/hadoop/迭代二_机器学习_数据契约.md`（输入/输出结构与类型，权威）；
> 要求与实现说明见 `docs/hadoop/迭代二_机器学习端侧开发要求.md`（§12）与 `docs/hadoop/迭代二_机器学习_实现方案.md`。
> 本文件**只写 Agent 与前端的设计**；端侧 ML 实现由 Hadoop 同学负责。
> 命名：本仓库平台那一轮称「迭代一重构」；本文件即课程口径的**迭代二**。

---

## 0. 分工与前提

- **端侧**实现 ML：单机训练（不提交集群作业），数据来自发布区 `ml1m-clean-v1`；模型与产物留在端侧。
- **本侧**实现：Agent 接入（工具表/结果/下载）+ 前端三视图 + 联调。
- Agent **只取结果与产物引用，不代理、不存储模型**；要取模型文件时经**模型下载路由**流式过手。

## 1. 对齐结论（已吸收端侧反馈）

1. **能力收敛为 6 项**：每类一个训练能力 + `algorithm` 参数（不再"每算法一个工具"）。
2. 产物条目：**`kind` = 文件格式**（`joblib`/`json`/`csv`/`jsonl`/`md`/`other`），**`role` = 用途**（`model`/`profile`/`labels`/`coords`/`meta`）。
3. **模型编号 = 任务编号**（同值，形如 `ml-YYYYMMDD-HHMMSS-xxxx`）；训练并入现有任务体系（**共用执行位、状态、取消、等待**）。
4. 预测编号 **字符串进、字符串出**。
5. 指标 **0–1 小数、保留四位**；前端显示百分比时自行 ×100。
6. **新增模型下载路由**（与任务产物/已发布数据同口径：同一口令、Host 白名单、流式）；模型**不内联进调用参数**。
7. **单机训练**；"作业聚合"为端侧可选配置项、**非工具参数**、**对端零改动**。
8. 加性新增错误码 `MODEL_NOT_FOUND`；**契约版本仍 `1.0`**。

## 2. Agent 侧设计

### 2.1 工具表（新增 6 项，`source=mcp`）

| 工具 | 参数 | 性质 |
|---|---|---|
| `train_classifier` | `algorithm ∈ {naive_bayes, random_forest}`；`data_version?`、`tag?`、`params?` | 长任务 |
| `cluster_users` | `algorithm ∈ {kmeans, hierarchical}`；`k?`、`data_version?`、`tag?`、`params?` | 长任务 |
| `reduce_dimensions` | `algorithm ∈ {pca, nmf}`；`n_components?`、`data_version?`、`tag?`、`params?` | 长任务 |
| `predict_high_rating` | `model_id`、`user_id`、`movie_id` | 同步 |
| `list_ml_models` | — | 只读 |
| `get_ml_model` | `model_id` | 只读 |

- 参数、默认、范围、`params` 白名单**逐字对齐**数据契约 §1（未知键 → `USAGE`；`k`∈[2,50] 两聚类共用；`n_components`∈[2,20]）。
- 训练类为**长任务**：异步受理 + 轮询查状态；沿用现有机制。
- **双编号**：`model_id` 即 `hadoop_task_id`（同值）；界面用 agent 编号，远端调用一律用 hadoop 编号（= `model_id`）。

### 2.2 结果与产物

- 结构化结果板块按 `task_type` 新增三类：`classification` / `clustering` / `dimensionality_reduction`（字段见数据契约 §3.2–§3.4）。
- 产物条目按 **`role` 分类**、按 **`kind` 判断能否在线预览**；模型本体（`role=model`、`kind=joblib`）**不可预览**，走下载。
- 下载：任务产物沿用 `/api/files/download`；**模型产物**经**模型下载路由**中转（Agent 带口令流式转给浏览器）。

### 2.3 边界（本侧不改）

- 算法白名单、`params` 白名单、`k`/`n_components` 范围、错误码语义——**以数据契约 §1/§5 为准**，本侧逐字对齐。

## 3. 前端设计

- 结果视图按 `task_type` 渲染：
  - **分类**：F1 / AUC / PR-AUC（**×100 显示百分比**）、混淆矩阵、特征重要性（朴素贝叶斯附 `feature_importance_note` 说明）、`split_sizes`、`leakage` 小字。
  - **聚类**：每群 `label` / `size` / `top_genres` / `behavior`；未传 `k` 时展示 `k_selection` 曲线。
  - **降维**：`comparison`（`before`/`after`/`agreement`）+ `explained_variance_ratio` / `reconstruction_error` / `resource`；用户坐标散点图。
- 资源页：模型列表（`list_ml_models`）与模型详情（`get_ml_model`）。
- 单位换算：服务端 0–1，前端 ×100。

## 4. 待端侧补充（本侧接线需要）

1. **模型下载路由的确切路径形状**（例如 `/download/model/{model_id}/{path}`）——用于中转接线与前端下载按钮。
2. 训练占用执行位期间：前端统一提示"已有任务在运行（占用者 X），等它结束再试"。

## 5. 验收

- **真数据联调**：分类 / 聚类 / 降维各跑通一次，产出真指标；结果板块与视图正确。
- 模型可经下载路由取用；双编号（`agent_task_id` ↔ `model_id`）正确。
- 两算法同数据、同口径；防泄漏可在 `leakage` 字段核查。

---

## 附：算法路径（端侧文档已详载）

| 任务 | 算法一 | 算法二 | 理由 |
|---|---|---|---|
| 分类 | 朴素贝叶斯 | 随机森林 | 生成式概率 vs 树集成，机制差异大 |
| 聚类 | K-Means | 凝聚层次（Ward） | 质心=可解释群体画像；层次=群体结构 |
| 降维 | PCA | NMF | 方差最大化 vs 部分式分解（可解释"典型口味"） |
