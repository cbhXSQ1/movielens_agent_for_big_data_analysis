# 迭代一 Hadoop 侧实施计划与技术方案（v1.0 · 已确认）

> **For agentic workers（执行方：Ubuntu 虚拟机内的开发 AI）:** 本计划按里程碑逐项执行；每个任务遵循 TDD（先写失败测试 → 最小实现 → 测试通过 → 提交），任务完成即提交（commit 信息在各任务中给出）。任何偏离计划的设计改动，先记录到 `docs/hadoop/decisions.md` 再实施。执行方式建议：superpowers:subagent-driven-development（推荐）或 executing-plans。
>
> 评审状态：**J1–J10 已全部确认**（见 §0）；本文件与 `docs/hadoop/agent-interface.md` 为开发依据。

**Goal:** 在 Hadoop 上实现迭代一的数据清洗与五维质量评分：按 `config/` 两份 v1 配置执行，产出清洗后数据、隔离区、五维前后分数、版本元数据与评估报告，并向 Agent 层提供稳定 CLI 接口。

**Architecture:** 纯 Python 核心引擎（配置加载 → 算子库 → 动作/策略 → 评分公式）+ Hadoop Streaming 多趟 MapReduce 作业（清洗、统计、评分）+ 编排 driver（提交、汇总、发布、报告）。本地 runner 与 Hadoop 作业共用同一引擎代码，保证"本地黄金测试 = 集群结果"。

**Tech Stack:** Hadoop 3.3.x（Streaming）、Java 11、Python 3.8+（仅标准库）、JSON 配置、文本数据格式（ISO-8859-1、`::` 分隔）、`unittest`。

## 全局约束（每个任务都适用）

- 数据清洗与评分**必须由 Hadoop 实际执行**；不得使用占位数据、模拟结果或主观推测
- 清洗前后评分使用同一份评分配置、同一套公式；分数只在同一 `(rule_version, scoring_scheme_version)` 内可比
- `config/*.v1.json` 为已登记默认方案，**只读**；任何修改 = 新版本文件
- 输入输出保持 ISO-8859-1 + `::` 文本格式；邮编按字符串处理
- 代码仅 Python 标准库（3.8+）；测试用 `unittest`
- 所有产物带版本元数据：`task_id / data_version / rule_version / scoring_scheme_version / policy_version / T1 / T2`
- 不得覆盖已发布版本；不得混用不同版本产物
- CLI 契约以 `docs/hadoop/agent-interface.md` v1.0 为准（字段名不得随意改）
- 所有命令在 Ubuntu 虚拟机内执行（Windows 侧不参与运行）

---

## 0. 评审结论（已确认）

| 项 | 结论 |
|---|---|
| J1 执行技术 | **Hadoop Streaming + Python**（map/reduce 脚本，标准输入输出；集群各节点需 Python 3.8+） |
| J2 集群环境 | **Ubuntu 虚拟机（非 WSL）单机伪分布式**；课程项目无真实集群；driver 与 Agent 同在 VM 内 |
| J3 仓库结构 | 含 `frontend/`；`docs/` 分 `hadoop/ agent/ frontend/` 三个子目录 |
| J4 隔离输出 | 同引擎**双模式两趟 map-only**（keep / quarantine） |
| J5 最终比率 | Hadoop 侧 `score_finalize` 作业产出最终分数 JSON |
| J6 Agent 接口 | **CLI + 共享 JSON 文件**（driver 子命令，stdout JSON 信封） |
| J7 发布与幂等 | `/data/tasks/<task_id>/` → `/data/published/<data_version>/`；已存在则比对内容哈希，一致复用、不一致报错 |
| J8 黄金基线 | 用本地原型实测数字断言（§7） |
| J9 数据格式 | 保持文本 `::` + ISO-8859-1 |
| J10 规模 | 全量 1,000,209 行；小样本仅用于集成测试 |

## 1. 运行环境（Ubuntu 虚拟机，伪分布式）

- 建议版本：Ubuntu 22.04+、Java 11（`openjdk-11-jdk`）、Hadoop 3.3.6
- 资源建议：≥4GB 内存（推荐 8GB）、≥2 核、≥25GB 磁盘；**安装前做虚拟机快照**
- 数据准备：把 Windows 上的 `ml-1m.zip` 拷入 VM，解压到 `~/data/raw/ml-1m/`（三个 `.dat` 文件）
- 角色分布（同机）：NameNode/DataNode/SecondaryNameNode/ResourceManager/NodeManager + driver + Agent
- 环境验收（M0.5 任务中执行）：
  1. `jps` 出现 5 个守护进程
  2. `hdfs dfsadmin -report` 正常
  3. 用一个 3 行 Python 脚本跑通 Streaming smoke（mapper+reducer）
  4. 记录 `hadoop-streaming` jar 路径到 `hadoop/scripts/env.sh`

## 2. 总体架构

```
                ┌─────────────── HDFS ───────────────┐
原始文件 ──上传──► /data/raw/ml-1m/                    │
                │      │                             │
                │      ▼                             │
                │ 清洗作业（Streaming 多趟）           │
                │  ① M/U 维表: normalize → resolve    │
                │  ② R 评分: validate → dedupe → cross│
                │      │              │               │
                │      ▼              ▼               │
                │ /data/tasks/<task_id>/cleaned/      │
                │ /data/tasks/<task_id>/quarantine/   │
                │      │                             │
                │      ▼                             │
                │ 评分作业（before=raw / after=cleaned）│
                │  measure → groupstats → finalize    │
                │      │                             │
                │      ▼                             │
                │ /data/tasks/<task_id>/metrics/*.json│
                │ metadata.json / report.json / .md   │
                └────────────────────────────────────┘
                          │
                发布: /data/published/<data_version>/
                          │
                  Agent 层（CLI + JSON，见接口规范）
```

## 3. 仓库结构

```
README.md
.gitignore
config/                          # 两份 v1 配置 + 说明（只读，两侧共用）
docs/
  hadoop/                        # 本组文档：plan.md / agent-interface.md / runbook.md / decisions.md
  agent/                         # Agent 组文档
  frontend/                      # 前端组文档
hadoop/
  engine/                        # 纯本地核心库（无 Hadoop 依赖）
    __init__.py  config_loader.py  operators.py  actions.py  pipeline.py  metrics.py
  jobs/                          # Streaming 入口（map/reduce 薄壳）
    _common.py
    users_normalize.py  users_resolve.py
    movies_normalize.py movies_resolve.py movies_residual.py
    ratings_validate.py ratings_dedupe.py ratings_cross.py
    stats_marks.py
    score_measure.py score_groupstats.py score_finalize.py
  driver/
    run_task.py                  # CLI（契约见 agent-interface.md）
  tools/
    validate_configs.py          # 已有
  tests/
    fixtures/                    # 小样本 + 期望输出
    test_*.py
  scripts/
    env.sh                       # HADOOP_HOME / STREAMING_JAR / 数据路径
    run_tests.sh
    upload_raw.sh
    run_full_task.sh
reference/                       # 本地原型参考实现与实测输出（语义依据）
agent/                           # Agent 组代码（本组不修改）
frontend/                        # 前端代码（本组不修改）
```

## 4. 核心接口（引擎内部，精确签名）

### 4.1 `engine/config_loader.py`

```python
@dataclass
class LoadedSchemes:
    rules: dict            # cleaning_rules.v1.json 内容
    scoring: dict          # scoring_scheme.v1.json 内容
    rules_path: str; scoring_path: str
    rule_version: str      # rules["version"]，如 "1.0.0"
    rule_hash: str         # 文件字节 sha256
    scoring_version: str; scoring_hash: str
    policy_version: str    # 所有 policy(ref,value) 排序后的 sha256
    t1: str; t2: str       # ISO8601 UTC

def load_schemes(rules_path: str, scoring_path: str) -> LoadedSchemes   # 校验失败抛 ConfigError(错误列表)
def validate_configs(rules: dict, scoring: dict) -> tuple[list[str], list[str]]  # (errors, warnings)
```

校验规则 = 现有 `hadoop/tools/validate_configs.py` 的全部检查项（维度固定、算子白名单、权重和为 1、policy.value ∈ options、pipeline 覆盖、reference_domains 一致等）。

### 4.2 `engine/operators.py`

```python
def evaluate(expr, fields: dict, ctx: dict) -> bool
# fields: 解析后的列 {列名: 字符串值}；ctx: {"reference_domains":..., "raw_line": str, "table": str}
# expr 为配置中的 detect AST；支持 and/or/not 递归；未知算子抛 ConfigError
OPERATORS: dict[str, Callable]   # 31 个算子（目录见 cleaning_rules.v1.说明.md §7）
```

实现注意：
- `field_count_ne` / `foreign_delimiter` 使用 `ctx["raw_line"]`
- `token_*` 类按 `sep` 拆分后判断
- `ref_missing` / `ref_exists` / `ref_unused` / `group_anomaly` 需要作业侧提供的上下文（维表集合、分组计数），本地由 pipeline 注入

### 4.3 `engine/actions.py`

```python
def make_quarantine_record(source_file, line_no, raw_line, rule_id, stage, reason, task_id) -> dict
def apply_fix(record: dict, fix_spec: dict, rules: dict) -> tuple[dict, list[str]]
# 支持策略：strip / decode_html_entity / decode_double_encoding / timestamp_ms_to_s /
#          zip_plus4_truncate / blank_invalid_field / prefer_valid_record / keep_first / field_level_merge
def resolve_records(records: list[dict], policy: dict, field_specs: list) -> tuple[dict, list[dict]]
# 返回 (保留记录, 隔离记录列表)；prefer_valid：按字段合法数排序；并列→字段级合并；完全并列→取原始行字典序最小（确定性）
```

### 4.4 `engine/pipeline.py`

```python
def run_local(raw_dir: str, schemes: LoadedSchemes, out_dir: str, task_id: str) -> dict
# 按 §5 顺序在单进程内跑完清洗+评分，产出与 Hadoop 相同的目录结构与 JSON；返回 counts/stats
```

### 4.5 `engine/metrics.py`

```python
def compute_metrics(records: dict, schemes: LoadedSchemes) -> dict   # {metric_id: 0..1}
def dimension_scores(metric_values: dict, schemes) -> dict           # {dim: 0..100}
def composite_score(dim_scores: dict, schemes) -> float
def finalize(metric_values: dict, schemes) -> dict  # 完整 before/after JSON（见接口文档 result.scores）
```

### 4.6 作业脚本约定（`jobs/_common.py`）

```python
# 编码：必须显式 ISO-8859-1（避免节点 locale 影响）
IN  = io.TextIOWrapper(sys.stdin.buffer,  encoding='iso-8859-1', newline='\n')
OUT = io.TextIOWrapper(sys.stdout.buffer, encoding='iso-8859-1', newline='\n')
# 计数器（Streaming 协议）：
def counter(group: str, name: str, n: int = 1):
    sys.stderr.write(f"reporter:counter:{group},{name},{n}\n")
# 引擎分发：driver 打包 engine/ + config/ 为 engine.zip，用 -files 分发；
# 脚本启动时 sys.path.insert(0, "engine.zip") 即可 zipimport。
```

## 5. Hadoop 作业设计

### 5.1 清洗作业（顺序：先维表后评分表）

| 序 | 作业 | 输入 | Map | Shuffle/Reduce | 输出 | 规则 |
|---|---|---|---|---|---|---|
| 1 | `users_normalize` | users.dat | 解析/校验/修复（U1/U2/U3） | 无（map-only，双模式） | cleaned + quarantine | U1–U3 |
| 2 | `users_resolve` | 上一步 cleaned | key=UserID | reduce 去重/保留合法/字段级合并（U5） | cleaned + quarantine | U5 |
| 3 | `movies_normalize` | movies.dat | 解析/修复（P1/M1/M2） | 无（双模式） | cleaned + quarantine | P1–M2 |
| 4 | `movies_resolve` | 上一步 cleaned | key=MovieID | reduce 去重/prefer_valid（M4） | cleaned + quarantine | M4 |
| 5 | `movies_residual` | 上一步 cleaned | 检查 M3/M6/M7/M9（标记；M8 数据留统计作业） | 无 | cleaned（含标记）+ counters | M3/M6/M7/M9 |
| 6 | `ratings_validate` | ratings.dat | 解析/校验/修复（P2/P3/R1–R5） | 无（双模式） | cleaned + quarantine | P2/P3/R1–R5 |
| 7 | `ratings_dedupe` | 上一步 cleaned | key=(UserID,MovieID,Timestamp) | reduce 去重+冲突兜底（R6/R7/R8） | cleaned + quarantine | R6–R8 |
| 8 | `ratings_cross` | 上一步 cleaned | 广播 users/movies 清洗后维表（`-files`） | 无（双模式） | cleaned + quarantine | X1/X2 |
| 9 | `stats_marks` | 清洗中间产物 | 按 UserID/Title 聚合（R9/M8） | reduce 统计异常组与同名组 | `stats.json` | R9/M8/X3 |

- **双模式**：同一引擎函数；`--mode keep` 输出修复后记录，`--mode quarantine` 输出隔离记录（`source_file,line_no,raw_line,rule_id,stage,reason`）。两模式判定互补性由单元测试保证。
- **确定性**：reduce 内先将 values 按原始行字典序排序再应用策略，保证重跑结果一致。
- **R4 在 R5 前、R6 在 R7 前**：由引擎阶段顺序固定。

### 5.2 评分作业（同一套作业跑两遍：before=raw / after=cleaned）

| 序 | 作业 | 逻辑 | 输出 |
|---|---|---|---|
| 1 | `score_measure` | map 发射各指标分子/分母计数（token 计数、字段计数、跨表检查需广播维表） | `counts.json` |
| 2 | `score_groupstats` | 按业务键 shuffle：唯一键数、重复组、冲突组、最大时间（F2） | `groupstats.json` |
| 3 | `score_finalize` | 计数 → 比率 → 维度分 → 综合分（同一公式两遍） | `metrics/before.json`、`metrics/after.json` |

- 结构类指标（C2/C3/S1）分母 = **原始行数**（raw 侧含坏行；cleaned 侧行=记录）。
- 最终分数必须由 `score_finalize` 产出（不得在 driver 内计算比率）。

### 5.3 典型提交命令（driver 内实现，供参考）

```bash
hadoop jar "$STREAMING_JAR" \
  -D mapreduce.job.name="iter1-users-normalize-keep" \
  -D mapreduce.job.reduces=0 \
  -files engine.zip,config/cleaning_rules.v1.json,jobs/users_normalize.py \
  -input  /data/raw/ml-1m/users.dat \
  -output /data/tasks/$TASK_ID/stage/users_normalize_keep \
  -mapper "python3 users_normalize.py --mode keep --rules cleaning_rules.v1.json"
```

## 6. 输出、元数据与发布

```
/data/tasks/<task_id>/
  status.json                    # 状态机（字段见接口文档）
  cleaned/{ratings,movies,users}.dat
  quarantine/{ratings,movies,users}.dat + quarantine_summary.json
  metrics/{before,after,composite}.json
  stats.json
  metadata.json                  # task_id/data_version/rule_version/scoring_scheme_version/policy_version/T1/T2/input_counts/output_counts
  report.json  report.md
/data/published/<data_version>/{ratings,movies,users}.dat + metadata.json   # 不可覆盖
```

- 发布规则：若 `/data/published/<data_version>/` 已存在 → 计算 cleaned 三表内容哈希与已发布一致则复用；不一致 → 任务失败并提示"配置或输入已变化，必须升 data_version"。
- 报告内容：数据量变化、各规则命中与处置、级联影响、五维前后分数与变化、评价局限、版本信息。

## 7. 测试与黄金基线

### 7.1 单元测试（`unittest`，无 Hadoop）
- 算子库：31 个算子逐用例（边界：空值、`3.5`、`five`、毫秒阈值 `100000000000`、`Ã©` 解码、ZIP+4、`[20XX]`）
- 动作/策略：修复策略、`prefer_valid_record`、`field_level_merge`、策略选项切换
- 配置校验：坏配置必须被拒（缺字段、坏算子、权重和≠1、维度被改）
- 评分公式：手算小样例 + 全量黄金值

### 7.2 集成测试（fixture，无 Hadoop）
- `tests/fixtures/`：约 60 行三表小数据，覆盖每类注入问题；期望 cleaned/quarantine/分数作为断言文件
- 作业脚本本地 stdin→stdout 测试：`cat fixture | python3 jobs/xxx.py --mode keep`

### 7.3 黄金测试（全量 ml-1m，本地 runner）

| 断言项 | 期望值 |
|---|---|
| 清洗后 评分/用户/电影 | 1,000,209 / 6,040 / 3,883 |
| R1 隔离 | 6,752（空 UserID 3,376 + 空 MovieID 3,376） |
| R2 隔离 | 43,885（0:8,252 / 6:8,252 / 3.5:13,503 / five:10,502 / 空:3,376） |
| R3 / R5 隔离 | 3,375 / 12,003 |
| R4 修复 | 13,503 |
| R6 去重 | 49,510 |
| X1 / X2 隔离 | 各 10,502（R1 后结算） |
| M1 / M4 移除 | 58 / 454 |
| U1 / U5 移除 | 72 / 726 |
| P1 / P2 / P3 | 48 / 6,075 / 7,606 |
| 五维前后 | Accurate 97.71→99.79；Complete 98.82→99.99；Unique 90.90→100；Up-to-date 98.22→100；Consistent 89.65→100 |
| 综合分 | 95.07 → 99.95 |

### 7.4 集群对账（M6）
- 集群 cleaned 三表内容哈希 = 本地 runner 输出哈希
- `metrics/*.json` 与黄金值一致；逐趟作业 counters 与 §7.3 命中数对账

## 8. 里程碑与任务清单（逐项开发）

> 每个任务：先写测试 → 跑失败 → 最小实现 → 跑通过 → 提交。

### M0.5 环境搭建（VM 内）
- **Files:** `hadoop/scripts/env.sh`
- **Steps:** 安装 Java 11；安装 Hadoop 3.3.6 到 `/opt/hadoop`；配置 4 个 XML（core-site/hdfs-site/mapred-site/yarn-site，伪分布式标准配置，replication=1）；`hdfs namenode -format`；`start-dfs.sh && start-yarn.sh`；`jps` 验证 5 进程；跑 Streaming smoke（3 行 mapper/reducer 的 wordcount）；记录 `STREAMING_JAR` 路径；解压 ml-1m 到 `~/data/raw/ml-1m/`
- **Acceptance:** `jps` 五进程齐全；smoke 作业输出正确词频；`env.sh` 内容正确
- **Commit:** `chore(env): pseudo-distributed hadoop setup notes & env.sh`

### M0 仓库骨架
- **Files:** 目录结构（§3）、`.gitignore`、`hadoop/scripts/run_tests.sh`
- **Steps:** 建目录与 `__init__.py`；`run_tests.sh` 执行 `python3 -m unittest discover -s hadoop/tests -v`；运行 `validate_configs.py`
- **Acceptance:** 校验 PASS；测试命令退出码 0
- **Commit:** `chore: hadoop-side repo skeleton`

### M1 引擎核心（TDD）
- **M1a `config_loader.py`**：加载+校验+版本哈希；测试：正常配置通过、篡改配置被拒（逐类错误）
  - **Commit:** `feat(engine): config loader with validation & version hashes`
- **M1b `operators.py`**：31 算子 + `evaluate`；测试：每算子 2+ 用例（含 §7.1 边界）
  - **Commit:** `feat(engine): operator library v1`
- **M1c `actions.py`**：修复策略、resolve 策略、隔离记录；测试：每策略用例 + 确定性（排序后取行）
  - **Commit:** `feat(engine): actions, fix strategies & policies`
- **Interfaces:** §4.1–4.3；下游（pipeline/jobs）只依赖这些签名

### M2 本地全流程 + 黄金测试
- **Files:** `engine/pipeline.py`、`engine/metrics.py`、`hadoop/tests/test_golden.py`、`hadoop/tests/fixtures/*`
- **Steps:** 实现 pipeline（§5 顺序、内存版）；实现 metrics；写黄金测试（§7.3 全表断言）；生成 fixture 与期望输出
- **Acceptance:** 黄金测试全绿（允许数分钟运行）；fixture 生成完毕
- **Commit:** `feat(engine): local pipeline & metrics`；`test: golden e2e + fixtures`

### M3 清洗作业（Streaming）
- **M3a `jobs/_common.py` + 维表 5 作业**：users_normalize/resolve、movies_normalize/resolve/residual
- **M3b `ratings_validate/dedupe/cross`**
- **M3c `stats_marks`**
- **Steps:** 每作业先写 stdin→stdout 测试（fixture），再实现；`--mode keep|quarantine` 互补性测试
- **Acceptance:** 本地测试全绿；小样本在集群上跑通（`run_task.py` 未完成前用裸 streaming 命令）
- **Commit:** `feat(jobs): dimension tables`；`feat(jobs): ratings pipeline`；`feat(jobs): stats & marks`

### M4 评分作业
- **Files:** `jobs/score_measure.py`、`score_groupstats.py`、`score_finalize.py`
- **Steps:** 本地测试（fixture 期望分数）→ 实现 → 集群跑 before/after → 与黄金分数一致
- **Commit:** `feat(jobs): scoring pipeline (measure/groupstats/finalize)`

### M5 编排 driver + 发布 + CLI
- **Files:** `hadoop/driver/run_task.py`
- **Steps:** 实现接口文档全部子命令（validate/schemes/start/status/result/samples/report/tasks）；异步执行（`start` 分离子进程，`status.json` 随阶段更新）；发布哈希逻辑；报告生成
- **Acceptance:** 契约测试（JSON 字段与接口文档示例一致）；重复运行内容哈希一致；版本冲突时报错
- **Commit:** `feat(driver): orchestration, publish & CLI contract`

### M6 集群全量运行与对账
- **Steps:** `upload_raw.sh` 上传；全量 `start`；哈希对账（§7.4）；写 `docs/hadoop/runbook.md`；记录权威结果到 `docs/hadoop/iteration-1-results.md`
- **Acceptance:** 对账通过；runbook 照做可复现
- **Commit:** `docs: runbook & iteration-1 results`

### M7 Agent 联调
- **Steps:** 与 Agent 组按接口文档联调；提供 `hadoop/tests/test_cli_contract.py` 模拟 Agent 调用序列（start→status→result→samples）
- **Acceptance:** 契约测试全绿；Agent 侧可完整走通
- **Commit:** `test: agent CLI contract`

### M8 演示支持
- **Steps:** 样例导出脚本（前端数据）、一键演示脚本、汇报数字表
- **Commit:** `chore: demo support`

## 9. 执行约定（给 VM 内开发 AI）

1. 按里程碑顺序执行；每个任务先测后码；**每个任务一次提交**（信息用各任务的 Commit 行）
2. 不改 `config/` 下文件；不覆盖已发布版本；不改 `agent/`、`frontend/`
3. 每完成一个里程碑：跑 `hadoop/scripts/run_tests.sh` + 全量黄金测试，把数字贴进提交说明
4. 遇到计划与实际不符：先在 `docs/hadoop/decisions.md` 记录（现象/选项/决定），再继续
5. 所有作业脚本必须通过"本地 stdin→stdout 测试"后才允许上集群
6. 参考实现：`reference/`（本地原型脚本与实测输出），语义冲突时以 `config/` 与本文档为准

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| 节点 Python 版本/编码差异 | 只用 3.8 兼容语法；显式 ISO-8859-1 读写（§4.6） |
| Streaming 多输出限制 | J4 双模式两趟 |
| reduce 结果不确定性 | values 排序后再应用策略（§5.1） |
| VM 资源不足 | ≥4GB 内存；replication=1；单 reducer |
| 发布被覆盖 | 内容哈希比对 + 版本冲突报错（§6） |
| 作业中途失败 | `status.json` 记录阶段与 stderr 摘要；可重跑（幂等） |
| 黄金数字漂移 | 任何改动后必须重跑黄金测试，数字不符即回退 |
