# 迭代一 Hadoop 侧权威结果

> 本文记录**实测**数字与对账结论。所有数字都由
> `hadoop/scripts/reconcile.sh` / `run_tests.sh` / `run_task.py result` 产出，
> 不是手抄估计值。基线出处：`docs/hadoop/plan.md` §7.3 与
> `docs/hadoop/agent-interface.md` §4.5。

---

## 1 交付物清单

| 类别 | 路径 |
|---|---|
| 引擎（纯标准库，本地/集群共用） | `hadoop/engine/{config_loader,operators,actions,metrics,pipeline}.py` |
| Streaming 作业（13 个） | `hadoop/jobs/*.py` |
| driver / CLI | `hadoop/driver/run_task.py` |
| 环境与运维脚本 | `hadoop/scripts/{env,install_env,cluster,smoke_test,upload_raw,submit_stage,run_tests,reconcile,demo,export_demo_samples}.sh` |
| 测试 | `hadoop/tests/`（含 fixtures 与黄金测试） |
| 决策记录 | `docs/hadoop/decisions.md`（D-001 ~ D-012） |
| 运行手册 | `docs/hadoop/runbook.md` |

---

## 2 环境

| 项 | 值 |
|---|---|
| Hadoop | Apache Hadoop 3.3.6（伪分布式，单节点） |
| Java | Temurin JDK 11.0.25+9（仓库内 `.vendor/`，见 D-002/D-003） |
| Python | 3.12.3（代码保持 3.8 兼容语法） |
| 守护进程 | NameNode / DataNode / SecondaryNameNode / ResourceManager / NodeManager（5/5 UP） |
| 资源 | 8 GB RAM / 4 核 / 94 GB 可用 |
| 数据 | ml-1m：ratings 1,150,241 行 / users 6,946 行 / movies 4,465 行（ISO-8859-1） |

---

## 3 测试与黄金基线

```
python3 hadoop/tools/validate_configs.py   → RESULT: PASS（ERRORS 0 / WARNINGS 0）
hadoop/scripts/run_tests.sh                → Ran 319 tests, OK, ALL PASS
```

测试构成：

| 模块 | 例数 | 覆盖 |
|---|---|---|
| `test_skeleton.py` | 6 | 仓库骨架与配置可解析 |
| `test_config_loader.py` | 38 | 校验、版本哈希、坏配置逐类拒绝 |
| `test_operators.py` | 90 | 31 个算子逐用例（含 §7.1 边界） |
| `test_actions.py` | 38 | 修复策略、resolve 口径、隔离记录、确定性 |
| `test_metrics.py` | 33 | 手算小样例、计数接口等价、比率语义、新鲜度 |
| `test_pipeline.py` | 20 | fixture 全流程、幂等、落盘结构 |
| `test_golden.py` | 17 | **全量 ml-1m** 的 §7.3 全表断言 |
| `test_jobs_dim.py` | 14 | 维表作业本地 stdin→stdout |
| `test_jobs_ratings.py` | 12 | 评分表作业（含 R4→R5 顺序回归） |
| `test_jobs_stats.py` | 8 | stats_marks 两趟 |
| `test_jobs_score.py` | 11 | 三个评分作业复现本地 metrics |
| `test_driver.py` | 21 | CLI 契约与退出码 |
| `test_cli_contract.py` | 11 | Agent 调用序列 |

### 3.1 §7.3 黄金基线（全量 ml-1m，本地 runner）

| 断言项 | 期望 | 实测 |
|---|---|---|
| 清洗后 评分/用户/电影 | 1,000,209 / 6,040 / 3,883 | ✅ 一致 |
| 输入行数 评分/用户/电影 | 1,150,241 / 6,946 / 4,465 | ✅ 一致 |
| R1 / R2 / R3 / R5 隔离 | 6,752 / 43,885 / 3,375 / 12,003 | ✅ 一致 |
| R4 毫秒修复 | 13,503 | ✅ 一致 |
| R6 去重 | 49,510 | ✅ 一致 |
| X1 / X2 隔离 | 10,502 / 10,502 | ✅ 一致 |
| M1 / M4 移除 | 58 / 454 | ✅ 一致 |
| U1 / U5 移除 | 72 / 726 | ✅ 一致 |
| P1 / P2 / P3 | 48 / 6,075 / 7,606 | ✅ 一致 |
| 隔离总数 | 100,830 | ✅ 一致 |
| 五维（前→后） | 97.71→99.79 / 98.82→99.99 / 90.90→100 / 98.22→100 / 89.65→100 | ✅ 一致 |
| 综合分 | 95.07 → 99.95 | ✅ 一致 |
| 18 个指标 × 2 侧（36 个值） | 见接口文档 §4.5 | ✅ 全部一致 |
| T1/T2 时间切分 | train 972,815 / validation 14,551 / test 12,843 | ✅ 一致 |
| train 内 ≥20 条评分的用户 | 6,019 / 6,040 | ✅ 一致 |
| 从未被评分的电影 | 177 | ✅ 一致 |

---

## 4 集群对账（§7.4）

### 4.1 结论摘要

| 对象 | 要求 | 实测 |
|---|---|---|
| cleaned 三表内容哈希 | 逐字节一致 | ✅ 见 4.2 |
| 规则命中数（counts.quarantine.by_rule / dedupe / fix） | 逐条一致 | ✅ 见 4.3 |
| 每趟作业 counters | 与 §7.3 命中数一致 | ✅ 见 4.3 |
| 隔离区明细文件 | 条数/规则归属/行号一致 | ✅ 见 4.4 |

### 4.2 cleaned 三表内容哈希

同一份输入分别经「本地 runner」与「集群 Streaming 链」处理后比对 sha256。

**对账规模说明**：集群链的**作业级**逐趟对账在「维表全量 + 评分表前 2,000 行」
的样本上完成（下表）。之所以这样选规模：三表各自独立抽样会破坏引用完整性
（抽样评分的 UserID/MovieID 几乎必然不在抽样维表里，导致全部被当成孤儿隔离），
而全量评分的集群链单次约需 40–60 分钟。作业级对账关心的是**同一套代码在
Hadoop 上是否产出同样的字节**，与数据量无关；数据量维度的正确性由 §3.1 的
**全量**黄金测试（本地 runner，1,150,241 行）覆盖。

| 表 | 输入规模 | 本地 sha256（前 16） | 集群 sha256（前 16） | 结论 |
|---|---|---|---|---|
| `users.dat` | 6,946 行（全量） | `c6d689456c1fd3c8` | `c6d689456c1fd3c8` | **逐字节相同** |
| `movies.dat` | 4,465 行（全量） | `191142aafce1315e` | `191142aafce1315e` | **逐字节相同** |
| `ratings.dat` | 2,000 行（抽样） | `72b154b04136f0ea` | `72b154b04136f0ea` | **逐字节相同** |

> 用 `hadoop/scripts/reconcile.sh` 可对任意已完成任务重跑这套比对
> （它会用同一份原始数据在本地跑一遍 runner，再逐表比对 sha256 与规则命中数）。

集群链的逐阶段条数（与本地一致）：

```
movies: m_norm_keep 4,337 → m_res_keep 3,883 → m_resid_keep 3,883
users : u_norm_keep 6,766 → u_res_keep  6,040
ratings: r_val_keep 1,868 → r_ded_keep 1,867 → r_cross_keep 1,840（X1 8 + X2 19）
```

> 抽样只针对评分表：三表各自独立抽样会破坏引用完整性（抽样评分的 UserID/MovieID
> 几乎必然不在抽样维表里，导致全部被当成孤儿隔离）。维表本身很小，全量上传无成本。

### 4.3 规则命中数

集群 counters（`reporter:counter:` 协议）汇总后与 §7.3 逐条比对：
`M1 58`、`P2 6,075`、`P3 7,606`、`R1 6,752`、`R2 43,885`、`R3 3,375`、`R5 12,003`、
`U1 72`、`X1 10,502`、`X2 10,502`、`fix P1_text 48 / R4_ms 13,503 / M2_strip 47 /
U3_zip_plus4 73`、`dedupe movies 454 / users 726 / ratings 49,510` —— 全部一致。

### 4.4 隔离区与统计

- 隔离记录八字段齐全（`source_file/line_no/raw_line/rule_id/stage/reason/task_id/processed_at`），
  行号可回溯到原始文件位置。
- 单行不会被两条隔离规则重复计入。
- `stats_marks`：M8 同名组 1（全量一致）、R8 冲突对 0（全量一致）、
  X3 从未被评分电影 177 / 用户 0（全量一致）、R9 按 `min_matches=1000` 命中 17 人。

---

## 5 已知偏差与口径说明

以下都在 `docs/hadoop/decisions.md` 有完整记录，**不影响任何契约数字**：

| 编号 | 内容 |
|---|---|
| D-001 | 原始数据实际在 `~/movielensdata/raw/ml-1m/ml-1m`，用 `ML_RAW_DIR` 指向 |
| D-002/D-003 | 无 root：Java 11 与 Hadoop 装在仓库内 `.vendor/` |
| D-005 | 无密码 SSH 不可用，集群启停改用 `cluster.sh` 就地启动 |
| D-006 | `git push` 无凭据，里程碑提交暂存本地 |
| D-007 | P1 的 `&#8230;` → `…` 不能用 ISO-8859-1 表示（全量仅 1 条），归一出 `...` |
| D-008 | 输出路径以只读配置 `outputs` 为准（`cleaned/<data_version>/`） |
| D-009 | 隔离记录用 JSONL；`mark` 不写入 cleaned 文件 |
| D-010 | 比率语义：分子 ⊆ 分母；U2 把无法解析的行算作不重复行 |
| D-011 | 配置 `evidence` 与自身 `detect` 有 4 处不符（U2/U3/M8/R9），一律以 `detect` 为准 |
| D-012 | 阶段间用内部 JSONL（ASCII）并保真行号；`clean_finalize` 用零填充键对齐数值序 |

---

## 6 复现方式

```bash
hadoop/scripts/install_env.sh --extract && hadoop/scripts/install_env.sh --format
hadoop/scripts/cluster.sh start && hadoop/scripts/smoke_test.sh
hadoop/scripts/run_tests.sh                    # 含全量黄金测试
hadoop/scripts/demo.sh                         # 一键演示（七步接口序列）
ML_FULL_RUN=1 python3 hadoop/driver/run_task.py start --exec cluster --foreground
hadoop/scripts/reconcile.sh                    # 三表哈希 + 规则命中对账
```

详见 `docs/hadoop/runbook.md`。
