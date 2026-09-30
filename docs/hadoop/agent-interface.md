# Agent 接口规范 v1.1（Hadoop 侧 ↔ Agent 侧）


> 契约文件：Hadoop 侧按本规范实现 `hadoop/driver/run_task.py`；Agent 侧按本规范封装工具。
> 本版本号 `interface_version = "1.1"`；破坏性变更必须升版本并在本文件记录变更日志。
> v1.1（2026-09-30）：**加性升级** —— `start --task-type clean`（只清洗）与
> `score`（独立评分）两个精细任务命令、新错误码 `DEPENDENCY_MISSING`；v1.0 的八个子命令
> 与所有字段原样不变。变更记录见 §9。

## 1. 角色与部署约定

| 角色 | 职责 |
|---|---|
| Hadoop 侧 | 提供 driver CLI：提交清洗/评分任务、维护状态、产出结果 JSON、发布数据版本 |
| Agent 侧 | 用自然语言组织任务：调用 CLI、读取 JSON、向用户解释；**不得编造分数或结果** |

部署约定（课程环境）：
- Hadoop（伪分布式）、driver、Agent 运行在**同一台 Ubuntu 虚拟机**内
- 共享目录：任务产物在 `<ML_VAR_DIR>/tasks/<task_id>/`（默认 `<repo>/var`）
- 环境变量：`ML_VAR_DIR`（默认 `<repo>/var`）、`HDFS_BASE`（默认 `/data`）、`STREAMING_JAR`（见 `hadoop/scripts/env.sh`）

## 2. 通用约定

- **stdout 只输出一个 JSON 信封**：`{"ok": true, ...}` 或 `{"ok": false, "error": {"code": "...", "message": "...", "stage": "..."}}`；日志走 stderr
- **退出码**：`0` 成功；`2` 参数/配置非法；`3` 任务失败；`4` 任务不存在；`5` 任务未完成（请求 result 时）；`6` 版本/发布冲突
- **编码**：数据文件 ISO-8859-1；JSON 输出 UTF-8
- **并发**：同一时刻只允许 1 个运行中任务；冲突时 `start` 返回错误码 `TASK_ALREADY_RUNNING`（可用 `--force` 忽略，不推荐）
- **幂等**：同输入 + 同配置重复运行，cleaned 三表内容哈希必须一致；发布版本不可覆盖（哈希不一致时报错，**仅集群模式**；`--exec local` 不发布，见 §4.5 与 decisions.md D-015）。D-017 起比对发生在**发布目录层面**：哈希清单随发布目录保存（`.published_hashes.json`），任何新任务发布前从 HDFS 读回比对 —— 「改规则 → 同 data_version 新任务重跑」同样会被 `VERSION_CONFLICT` 拦住 |
- **不编造**：任务失败或未完成时，`result` 必须拒绝返回（退出码 3/5），只给状态与原因

## 3. 命令总览

```bash
python3 hadoop/driver/run_task.py <subcommand> [options]
```

| 子命令 | 用途 |
|---|---|
| `validate` | 校验两份配置（可含自定义配置），不执行任务 |
| `schemes` | 列出已登记方案（默认方案 + 自定义方案文件） |
| `start` | 发起清洗+评分任务（默认异步） |
| `status` | 查询任务状态 |
| `result` | 获取任务结果（仅成功任务） |
| `samples` | 获取清洗样本或隔离样本 |
| `report` | 获取评估报告（md/json） |
| `tasks` | 列出最近任务 |
| `start --task-type clean` | **v1.1** 只清洗不评分不发布（§4.3） |
| `score` | **v1.1** 独立评分（raw / published / task，§4.9） |

## 4. 子命令详表

### 4.1 `validate`

```bash
run_task.py validate --rules config/cleaning_rules.v1.json --scoring config/scoring_scheme.v1.json
```

```json
{
  "ok": true,
  "interface_version": "1.1",
  "errors": [],
  "warnings": [],
  "versions": {"rule": "1.0.0", "scoring": "1.0.0"}
}
```
失败示例：`{"ok": false, "error": {"code": "CONFIG_INVALID", "message": "...", "details": ["..."]}}`（退出码 2）

### 4.2 `schemes`

```json
{
  "ok": true,
  "schemes": [
    {"scheme_id": "ml1m-cleaning-default", "type": "cleaning", "version": "1.0.0", "status": "registered_default",
     "path": "config/cleaning_rules.v1.json", "description": "..."},
    {"scheme_id": "ml1m-quality-default", "type": "scoring", "version": "1.0.0", "status": "registered_default",
     "path": "config/scoring_scheme.v1.json", "description": "..."}
  ]
}
```

### 4.3 `start`

```bash
run_task.py start --rules config/cleaning_rules.v1.json --scoring config/scoring_scheme.v1.json \
  [--data-version ml1m-clean-v1] [--tag demo] [--foreground] [--scope full|sample]
```

```json
{
  "ok": true,
  "task_id": "20260924-101530-7f3a2c",
  "status": "queued",
  "task_dir": "/home/student/movielens_agent_for_big_data_analysis/var/tasks/20260924-101530-7f3a2c",
  "started_at": "2026-09-24T10:15:30Z"
}
```

- 默认异步：立即返回，后台执行；`--foreground` 阻塞到完成（调试用）
- 校验配置失败 → 退出码 2；已有运行中任务 → 错误码 `TASK_ALREADY_RUNNING`
- **`--scope`（D-016）**：`full`（**默认**）= 全量 1,150,241 行输入，正式口径，约 8 分钟；
  `sample` = 评分表前 2000 行（维表全量），仅联调用。口径写入 `status.json.scope`，
  前端据此（或 `counts.input.ratings_lines`）挂「抽样运行」横幅
- **`--task-type full|clean`（v1.1，默认 `full`）**：
  - `full` = 现有全流程（清洗 + 双侧评分 + 发布）；
  - `clean` = **只跑清洗链**（上传 → 三表清洗 → 统计 → 取回 + 隔离分拣），
    **不评分、不发布**（sample 口径同样不发布）。产物：`cleaned/`、`quarantine/`、
    `counts.json`、`stats.json`；`result` 的 `scores` 为空并如实标注
    `"scored": false` + `note: "clean 任务未评分（未发布）"`。
    阶段序列（status 进度用 clean 子集）：
    `queued → clean_users → clean_movies → clean_ratings → stats_marks → finalize → done`
  - 依赖与互斥与 full 相同：占用任务锁、原始数据可读

### 4.4 `status`

```bash
run_task.py status --task-id 20260924-101530-7f3a2c
```

```json
{
  "ok": true,
  "task_id": "20260924-101530-7f3a2c",
  "status": "running",
  "stage": "score_after",
  "stage_index": 6,
  "stage_total": 9,
  "progress_percent": 67,
  "message": "running score_after",
  "started_at": "2026-09-24T10:15:30Z",
  "updated_at": "2026-09-24T10:21:07Z",
  "errors": []
}
```

- `status ∈ {queued, running, succeeded, failed}`
- 阶段序列（stage 取值）：`queued → clean_users → clean_movies → clean_ratings → stats_marks → score_before → score_after → finalize → publish → done`
- `stage_total` = **工作阶段数（9）**：阶段序列共 10 项，`queued` 是起始状态、不计入；
  `progress_percent = round(100 × stage_index / stage_total)`，上例 `round(100×6/9) = 67`
- 失败时：`status="failed"`，`errors=[{"stage":"clean_ratings","message":"stderr 摘要"}]`
  （`stage` 是失败时所在的**阶段名**；`job`/`exit_code` 仅在 status.json 已有这些字段时透传，
  driver 目前不写，字段可省略）

### 4.5 `result`

```bash
run_task.py result --task-id 20260924-101530-7f3a2c
```

仅当 `status=succeeded` 时返回（否则退出码 5/3）。**完整字段示例（数值为原型实测，实际以任务输出为准）：**

```json
{
  "ok": true,
  "interface_version": "1.1",
  "task_id": "20260924-101530-7f3a2c",
  "status": "succeeded",
  "data_version": "ml1m-clean-v1",
  "versions": {
    "rule": {"version": "1.0.0", "sha256": "<hex>"},
    "scoring": {"version": "1.0.0", "sha256": "<hex>"},
    "policy": {"sha256": "<hex>"},
    "operator_library": "ml1m-ops.v1"
  },
  "time_boundaries": {"T1": "2001-12-31T23:59:59Z", "T2": "2002-06-30T23:59:59Z"},
  "counts": {
    "input":  {"ratings_lines": 1150241, "users_lines": 6946, "movies_lines": 4465},
    "output": {"ratings": 1000209, "users": 6040, "movies": 3883},
    "quarantine": {
      "total": 100830,
      "by_rule": {"P2": 6075, "P3": 7606, "R1": 6752, "R2": 43885, "R3": 3375, "R5": 12003,
                   "X1": 10502, "X2": 10502, "M1": 58, "U1": 72}
    },
    "dedupe": {"ratings": 49510, "movies": 454, "users": 726},
    "fix":    {"P1_text": 48, "R4_ms": 13503, "M2_strip": 47, "U3_zip_plus4": 73}
  },
  "scores": {
    "before": {"Accurate": 97.71, "Complete": 98.82, "Unique": 90.90, "Up-to-date": 98.22, "Consistent": 89.65, "composite": 95.07},
    "after":  {"Accurate": 99.79, "Complete": 99.99, "Unique": 100.0, "Up-to-date": 100.0, "Consistent": 100.0, "composite": 99.95},
    "delta":  {"Accurate": 2.08, "Complete": 1.17, "Unique": 9.10, "Up-to-date": 1.78, "Consistent": 10.35, "composite": 4.88},
    "metrics": {
      "before": {"A1": 96.14, "A2": 99.70, "A3": 98.20, "A4": 97.56, "A5": 97.36, "A6": 97.63,
                  "C1": 99.70, "C2": 97.65, "C3": 98.82, "U1": 90.81, "U2": 92.50, "U3": 89.50,
                  "F1": 97.46, "F2": 100.0, "S1": 98.82, "S2": 98.91, "S3": 98.81, "S4": 68.20},
      "after":  {"A1": 100.0, "A2": 100.0, "A3": 98.60, "A4": 100.0, "A5": 100.0, "A6": 100.0,
                  "C1": 99.99, "C2": 99.99, "C3": 100.0, "U1": 100.0, "U2": 100.0, "U3": 100.0,
                  "F1": 100.0, "F2": 100.0, "S1": 100.0, "S2": 100.0, "S3": 100.0, "S4": 100.0}
    }
  },
  "quarantine_summary": [
    {"rule_id": "R2", "name": "评分值域校验", "count": 43885, "samples": ["0", "6", "3.5", "five", ""]},
    {"rule_id": "R6", "name": "评分重复去重", "count": 49510, "samples": []}
  ],
  "paths": {
    "task_dir": "...", "cleaned_dir": "...", "quarantine_dir": "...", "metrics_dir": "...",
    "report_md": "...", "report_json": "...", "published_dir": "..."
  },
  "limitations": [
    "用户属性为自愿填写，未核验，A3/C1 不封顶是诚实口径",
    "U3/S4 等提升部分来自隔离出分母而非修复，详见报告",
    "时效性以数据集发布语境评估，以现实时间衡量必然过时"
  ]
}
```

> `by_rule` 为**结算后**命中数（按执行顺序，前面规则已处理的不重复计）。`R6` 属于去重不属隔离，仅出现在 `quarantine_summary` 的样例说明中（可省略）。
>
> **`paths.published_dir` 仅集群模式（`--exec cluster`）非空**；`--exec local`
> 不发布（发布是共享存储分发环节；本地模式产物全在任务目录，且不得依赖
> Hadoop，见 decisions.md D-015），此时该字段为 `null`。

### 4.6 `samples`

```bash
run_task.py samples --task-id <id> --type quarantine --table ratings --n 5
run_task.py samples --task-id <id> --type cleaned --table movies --n 5
```

```json
{
  "ok": true,
  "task_id": "...",
  "type": "quarantine",
  "table": "ratings",
  "total_available": 100830,
  "samples": [
    {"line_no": 13, "raw_line": "5::1722::2::978246108::EXTRA_FIELD", "rule_id": "P3", "stage": "parse", "reason": "字段数异常"},
    {"line_no": 47, "raw_line": "25,688,4,978133460", "rule_id": "P2", "stage": "parse", "reason": "异常分隔符"}
  ]
}
```

### 4.7 `report`

```bash
run_task.py report --task-id <id> --format md     # 输出报告全文
run_task.py report --task-id <id> --format json   # 输出报告 JSON
```

### 4.8 `tasks`

```json
{"ok": true, "tasks": [{"task_id": "...", "status": "succeeded", "started_at": "...", "data_version": "ml1m-clean-v1"}]}
```

### 4.9 `score`（v1.1：独立评分）

```bash
run_task.py score --source raw|published|task [--from-task <id>]
                  [--scoring config/scoring_scheme.v1.json] [--foreground] [--tag dbg]
```

- 定位：把「评分」从 full 流程里拆成独立任务，让 Agent 能按需组合
  （如 `start --task-type clean` → `score --source task --from-task <id>`）
- `--source` 三选一（**评分侧由 source 隐式决定，拍板 #9 —— 无显式 before/after 参数**）：
  - `raw`：对 HDFS 原始三表评分（等价 full 的 `score_before` 侧，同输入同公式）；
    产物写 `metrics/before.json`
  - `task`：对 `--from-task` 的中间流评分（等价 full 的 `score_after` 侧）；
    产物写 `metrics/after.json`
  - `published`：对发布区三表评分（该发布版本交付数据的独立质量评分）；
    产物写 `metrics/after.json`
- 前置依赖（缺失 → **`DEPENDENCY_MISSING`，退出码 2**，message 写清缺什么）：
  - `task`：`--from-task` 必填、须 `succeeded` 且 cleaned 产物存在
  - `published`：发布区 `/data/published/<data_version>/` 存在
  - `raw`：原始数据可读
- 互斥：`score` 与 `start` 一样**占用任务锁**（与其它写任务串行）
- 任务信封与 status 沿用现有形状：`task_id`、status 阶段序列
  `queued → score → done`（`stage_total=2`）；产物在任务目录：
  `metrics/<side>.json`、`counts.json`、`result.json`
- 成功示例（`result`）：

```json
{
  "ok": true,
  "task_id": "20260930-...",
  "status": "succeeded",
  "task_type": "score",
  "source": "raw",
  "scores": {"before": {"Accurate": 97.71, "...": "...", "composite": 95.07},
             "metrics": {"before": {"A1": 96.14, "...": "..."}}},
  "counts": {"output": {"ratings": 1150241, "users": 6946, "movies": 4465}},
  "paths": {"task_dir": "...", "metrics_dir": "...", "report_md": null, "report_json": null}
}
```

（`scored: true` 亦写入；`clean` 任务则相反，见 §4.3）

### 4.10 附加工具 `quick_clean`（演示性数据清洗，**不属于** v1.1 子命令集）

> 定位：**附加工具**，非契约子命令。用于现场演示、前端取数、快速预览——
> 不经过 Hadoop，直接调本地 runner，秒级到 90 秒出结果，**与集群任务同引擎同数**。

```bash
python3 hadoop/tools/quick_clean.py [--raw DIR] [--out DIR] [--sample N]
                                    [--task-id T] [--quiet]
```

| 参数 | 说明 |
|---|---|
| `--raw` | 原始数据目录（含三个 `.dat`）；缺省 `$ML_RAW_DIR` |
| `--out` | 输出目录；缺省 `<repo>/.demo/quick/`（已 gitignore） |
| `--sample N` | **只**抽评分表前 N 行、维表保持全量（抽样不破坏引用完整性） |
| `--task-id` | 写入 summary 的任务标识（装饰用） |
| `--quiet` | 不打印进度（进度走 stderr，stdout 永远只有一个 JSON 信封） |

**成功**（退出码 `0`）：

```json
{
  "ok": true,
  "interface_version": "1.1",
  "summary": {
    "task_id": "quick", "data_version": "ml1m-clean-v1",
    "counts": {"input": {...}, "output": {"ratings": 1000209, "users": 6040, "movies": 3883},
               "quarantine": {...}, "dedupe": {...}, "fix": {...}},
    "scores": {"before": {...}, "after": {...}, "delta": {...}, "metrics": {"before": {...}, "after": {...}}},
    "rule_hits": {...}, "detail": {...},
    "out_dir": "/home/.../movielens_agent_for_big_data_analysis/.demo/quick",
    "sample": 0
  }
}
```

- `counts` / `scores` 与 `result`（§4.5）**同构同源**：内部直接调
  `engine.pipeline.run_local`，同一引擎、同一条黄金测试路径
- 产物与集群任务同构：`<out>/cleaned/`、`<out>/metrics/`、`<out>/quarantine/`

**失败**（退出码 `2`）：

```json
{"ok": false, "error": {"code": "...", "message": "..."}}
```

约束：**不改任何数据**（输出都写到 `--out` 或 `.demo/`），**零 Hadoop 依赖**，
不触碰 `config/`、`agent/`、`frontend/`。

## 5. 状态文件（driver 内部与 Agent 可读）

`<task_dir>/status.json`（与 `status` 子命令同字段）；`<task_dir>/metadata.json` 含 `task_id/data_version/rule_version/scoring_scheme_version/policy_version/T1/T2/input_counts/output_counts`。

## 6. 错误码表

| code | 含义 |
|---|---|
| `CONFIG_INVALID` | 配置校验失败（details 列出问题） |
| `TASK_ALREADY_RUNNING` | 已有运行中任务 |
| `TASK_NOT_FOUND` | task_id 不存在 |
| `TASK_NOT_FINISHED` | 任务未完成（result 请求） |
| `TASK_FAILED` | 任务失败（含 stage/job/stderr 摘要） |
| `VERSION_CONFLICT` | 发布哈希冲突（配置/输入变化但版本未升） |
| `DEPENDENCY_MISSING` | **v1.1** 精细任务前置依赖缺失：score(task) 缺任务/缺产物、score(published) 发布区不存在（退出码 2，message 写清缺什么） |
| `USAGE` | 参数用法错误 |

## 7. Agent 侧工具映射建议（对应课程"Agent 工具"要求）

| Agent 工具 | 调用 |
|---|---|
| `start_cleaning_task(rules?, scoring?, data_version?, tag?)` | `start`（返回 task_id，立即回给用户"任务已提交"） |
| `get_task_status(task_id)` | `status`（用于"执行情况"展示） |
| `get_task_result(task_id)` | `result`（用于五维对比、数据量变化、隔离统计） |
| `list_schemes()` | `schemes`（解释"已登记默认方案"） |
| `get_samples(task_id, type, table, n)` | `samples`（回答"给我看异常记录"） |
| `validate_config(rules, scoring)` | `validate`（用户自定义配置时先校验） |
| `get_report(task_id, format)` | `report` |
| `quick_clean_demo(raw_dir?, sample?)` | **附加工具** `quick_clean`（见 §4.10）：秒级拿到干净数据与五维分数，用于现场演示或追问前的快速预览 |
| `start_clean_task(...)` | **v1.1** `start --task-type clean`：只清洗不评分不发布（§4.3） |
| `score_task(source, from_task?, scoring?)` | **v1.1** `score`：独立评分（§4.9） |

Agent 行为要求：任务失败/未完成时如实返回状态与原因；解释结果必须引用 `result` 中的实际数字与 `limitations`。

> **权威结果以 `start`+`result` 为准**：`quick_clean` 与集群任务同引擎同数，
> 但它不维护任务状态、不做发布与版本冲突保护，只适合演示与快速取数。

## 8. 端到端示例（Agent 一次完整交互）

```text
1) start --rules config/cleaning_rules.v1.json --scoring config/scoring_scheme.v1.json
   → {"ok":true,"task_id":"20260924-101530-7f3a2c","status":"queued",...}
2) status --task-id 20260924-101530-7f3a2c      （轮询展示进度，如 67% score_after）
3) result --task-id 20260924-101530-7f3a2c      （成功：五维对比 + 数据量 + 隔离统计）
4) samples --task-id ... --type quarantine --table ratings --n 5   （回答追问）
5) report --task-id ... --format md             （获取完整报告）
```

## 9. 变更日志

| 版本 | 日期 | 说明 |
|---|---|---|
| 1.0 | 2026-09-24 | 初版：8 个子命令、状态机、result schema、错误码 |
| 1.0 + 附加 | 2026-09-24 | **非破坏性新增**附加工具 `quick_clean`（§4.9）：演示性数据清洗，不属于 8 个子命令集；八个子命令与所有字段原样未动，无需改版本号 |
| 1.0 勘误 | 2026-09-27 | §4.4 状态示例与实现对齐：`stage_total` 为 9（`queued` 起始状态不计入，序列共 10 项）、`stage` 只取阶段名、`progress_percent = round(100×index/total)`、失败 `errors` 形态（`job`/`exit_code` 仅透传） |
| 1.0 + scope | 2026-09-29 | **`start` 新增 `--scope full\|sample`（默认 `full`）**：全量为正式口径（约 8 分钟），样本仅联调。此前未声明默认口径、driver 实质默认样本（`ML_FULL_RUN=1` 才全量），与"集群 = 正式"的预期不符 —— 见 decisions.md D-016。信封结构不变（`status.json` 增 `scope` 字段） |
| 1.1 | 2026-09-30 | **加性升级**：`start --task-type clean`（§4.3）与 `score --source raw\|published\|task`（§4.9）两个精细任务命令；新错误码 `DEPENDENCY_MISSING`（退出码 2）；`interface_version` 升 `1.1`。v1.0 八个子命令与字段原样不变；`--task-type` 与 `score` 的依赖/互斥规则与 Agent 策略层同源（见 driver-refactor-plan §7.2） |
