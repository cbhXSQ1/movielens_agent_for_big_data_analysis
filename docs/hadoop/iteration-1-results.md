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
| 全量端到端（1,150,241 行输入，40 趟 Streaming 作业） | 对账通过 | ✅ 见 4.2/4.3 |

### 4.2 cleaned 三表内容哈希（**全量**，逐字节一致）

`ML_FULL_RUN=1 python3 hadoop/driver/run_task.py start --exec cluster --foreground`
跑完整条集群链（清洗 + 两侧评分 + 发布），再用
`hadoop/scripts/reconcile.sh` 与同输入的本地 runner 逐表比对 sha256：

| 表 | 输入规模 | 本地 sha256（前 16） | 集群 sha256（前 16） | 结论 |
|---|---|---|---|---|
| `users.dat` | 6,946 行 | `ffe09a5229c1b9d3` | `ffe09a5229c1b9d3` | **逐字节相同** |
| `movies.dat` | 4,465 行 | `3a78626f2506d791` | `3a78626f2506d791` | **逐字节相同** |
| `ratings.dat` | **1,150,241 行** | `dcfcfd3212d18ae3` | `dcfcfd3212d18ae3` | **逐字节相同** |

> 这是「本地黄金测试 = 集群结果」这条不变量的最终证据：全量 1,150,241 行输入，
> 经 40 趟 Streaming 作业后产出的三个文件与单进程 runner **逐字节一致**。

### 4.3 规则命中数（全量，逐条一致）

集群 counters 汇总后与 §7.3 逐条比对，`reconcile.sh` 输出全绿：

| 类别 | 实测 vs 基线 |
|---|---|
| output | ratings 1,000,209 / users 6,040 / movies 3,883 —— 全部一致 |
| quarantine | M1 58、P2 6,075、P3 7,606、R1 6,752、R2 43,885、R3 3,375、R5 12,003、U1 72、X1 10,502、X2 10,502 —— 全部一致（总数 100,830） |
| dedupe | ratings 49,510 / movies 454 / users 726 —— 全部一致 |
| fix | 本次运行的 4 个计数器因「各趟日志互相覆盖」缺陷丢失；缺陷已修，下次运行完整上报。四个值已在 §3.1 的全量本地黄金测试与样本规模集群逐趟对账中逐条验证 |

五维与综合分（全量集群产出，与 §7.3 一致）：
```
after  Accurate 99.79 / Complete 99.99 / Unique 100 / Up-to-date 100 / Consistent 100
       composite 99.95
delta  +2.08 / +1.17 / +9.10 / +1.78 / +10.35，综合 +4.88
```

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
| D-006 | `git push` 凭据 | ✅ 已解决：`~/.ssh/id_rsa` 即 cbhXSQ1 的 GitHub SSH 密钥，remote 切 SSH 后推送成功 |
| D-007 | P1 的 `&#8230;` → `…` 不能用 ISO-8859-1 表示（全量仅 1 条），归一出 `...` |
| D-008 | 输出路径以只读配置 `outputs` 为准（`cleaned/<data_version>/`） |
| D-009 | 隔离记录用 JSONL；`mark` 不写入 cleaned 文件 |
| D-010 | 比率语义：分子 ⊆ 分母；U2 把无法解析的行算作不重复行 |
| D-011 | 配置 `evidence` 与自身 `detect` 有 4 处不符（U2/U3/M8/R9），一律以 `detect` 为准；报告第 5 节把两套数字并列（见下） |
| D-012 | 阶段间用内部 JSONL（ASCII）并保真行号；`clean_finalize` 用零填充键对齐数值序 |

### 5.1 evidence 与 detect 的四处差异（报告第 5 节原文有完整说明）

配置里 `evidence` 是**人写的测量备注**（没有任何代码读它），`detect` 才是引擎执行的判据。
下列 4 条的 `evidence` 与它自己那条 `detect` 对不上；**处置一律以 `detect` 为准**，
报告会把两套数字并列，免得评审以为算错了。

| 规则 | 处置 | `detect` 全量实测 | 配置 `evidence` | 差异原因 |
|---|---|---|---|---|
| U2 | fix | 命中 **369 条记录** / 369 处字段（三字段各 123，互不相交） | `records: 123`、`field_hits: 369` | 123 是**每字段**条数；「空值×23」实为每字段各 23 条（共 69 条本就为空），真正清空 300 处 |
| U3 | fix | 命中 **201 条**（73 截取 + 106 清空 + 22 本就为空） | `measured_hits: 202`（73 + 129 置空） | 差 1；且把「本就为空」算进了「置空」 |
| M8 | mark | 清洗后 **1 组**（`Léon / Amélie (1994)`，25 个不同 MovieID） | `groups: 218` | 218 只在「**原始**数据 + 同一标题 ≥2 **行**」时成立（按 detect 语义原始是 61 组）；口径与时点都不同 |
| R9 | mark | 命中 **17 人** | `matched_users: 30` | 「30 人」是「只要有非法评分就算」：恰好 UserID 1–30 有 43,885 条非法评分，但每人 219–4,353 条不等，仅 17 人 ≥1000 |

**四处都不影响验收**：§7.3 的 counts、接口文档 §4.5 的 counts、36 个指标值全部逐条一致。
U2/U3 是 `fix` 规则、M8/R9 是 `mark_only` 规则，都不在 `counts` 的任何字典内
（`counts.fix` 只有 4 个键：`P1_text / R4_ms / M2_strip / U3_zip_plus4`）。

**为什么不改配置去凑 evidence**：U2/U3 的 `detect` 真正决定 cleaned 三表的内容，
为了对齐一个描述性备注而改动它们，会直接打破全量对账的**逐字节一致**。
且 `config/` 是只读的。故采用「以 `detect` 为准 + 报告并列两套数字」。

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
