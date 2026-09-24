# Agent 接口规范 v1.0（Hadoop 侧 ↔ Agent 侧）

> 契约文件：Hadoop 侧按本规范实现 `hadoop/driver/run_task.py`；Agent 侧按本规范封装工具。
> 本版本号 `interface_version = "1.0"`；破坏性变更必须升版本并在本文件记录变更日志。

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
- **幂等**：同输入 + 同配置重复运行，cleaned 三表内容哈希必须一致；发布版本不可覆盖（哈希不一致时报错）
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

## 4. 子命令详表

### 4.1 `validate`

```bash
run_task.py validate --rules config/cleaning_rules.v1.json --scoring config/scoring_scheme.v1.json
```

```json
{
  "ok": true,
  "interface_version": "1.0",
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
  [--data-version ml1m-clean-v1] [--tag demo] [--foreground]
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

### 4.4 `status`

```bash
run_task.py status --task-id 20260924-101530-7f3a2c
```

```json
{
  "ok": true,
  "task_id": "20260924-101530-7f3a2c",
  "status": "running",
  "stage": "ratings_dedupe",
  "stage_index": 6,
  "stage_total": 10,
  "progress_percent": 60,
  "message": "running ratings_dedupe (keep)",
  "started_at": "2026-09-24T10:15:30Z",
  "updated_at": "2026-09-24T10:21:07Z",
  "errors": []
}
```

- `status ∈ {queued, running, succeeded, failed}`
- 阶段序列（stage 取值）：`queued → clean_users → clean_movies → clean_ratings → stats_marks → score_before → score_after → finalize → publish → done`
- 失败时：`status="failed"`，`errors=[{"stage":"ratings_dedupe","job":"ratings_dedupe","exit_code":1,"message":"stderr 摘要"}]`

### 4.5 `result`

```bash
run_task.py result --task-id 20260924-101530-7f3a2c
```

仅当 `status=succeeded` 时返回（否则退出码 5/3）。**完整字段示例（数值为原型实测，实际以任务输出为准）：**

```json
{
  "ok": true,
  "interface_version": "1.0",
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

Agent 行为要求：任务失败/未完成时如实返回状态与原因；解释结果必须引用 `result` 中的实际数字与 `limitations`。

## 8. 端到端示例（Agent 一次完整交互）

```text
1) start --rules config/cleaning_rules.v1.json --scoring config/scoring_scheme.v1.json
   → {"ok":true,"task_id":"20260924-101530-7f3a2c","status":"queued",...}
2) status --task-id 20260924-101530-7f3a2c      （轮询展示进度，如 60% ratings_dedupe）
3) result --task-id 20260924-101530-7f3a2c      （成功：五维对比 + 数据量 + 隔离统计）
4) samples --task-id ... --type quarantine --table ratings --n 5   （回答追问）
5) report --task-id ... --format md             （获取完整报告）
```

## 9. 变更日志

| 版本 | 日期 | 说明 |
|---|---|---|
| 1.0 | 2026-09-24 | 初版：8 个子命令、状态机、result schema、错误码 |
