# 迭代一 Hadoop 侧 Runbook（照做可复现）

> 目标：在一台干净的 Ubuntu 虚拟机上，从零复现「清洗 + 五维评分 + 发布」全流程，
> 并验证本地结果与集群结果一致。
> 环境相关的偏差与决定见 `docs/hadoop/decisions.md`（D-001 ~ D-012）。

---

## 0. 前置条件

| 项 | 要求 | 检查命令 |
|---|---|---|
| 操作系统 | Ubuntu 22.04（其他 Linux 亦可） | `lsb_release -a` |
| Python | 3.8+（本机实测 3.12.3） | `python3 -V` |
| 原始数据 | ml-1m 三个 `.dat`，ISO-8859-1 | `ls ~/data/raw/ml-1m/` |
| 磁盘 | ≥ 5 GB（含 Hadoop + JDK + 数据） | `df -h .` |
| 内存 | ≥ 4 GB（伪分布式 + 单 reducer） | `free -h` |

> **本仓库的安装是"仓库内自包含"的**：Java 11 与 Hadoop 3.3.6 装在
> `<repo>/.vendor/`，不需要 root、不需要改 `/etc`（D-002/D-003）。
> 若你的机器可以 `apt install`，也可以按官方文档装到 `/opt`，
> 只要相应设置 `JAVA_HOME` / `HADOOP_HOME` 环境变量即可。

---

## 1. 安装环境（幂等，可重复执行）

```bash
cd <repo>

# 1) 下载并解压 Java 11 与 Hadoop 3.3.6 到 .vendor/（约 1.7 GB）
hadoop/scripts/install_env.sh --extract

# 2) 渲染配置：core-site / hdfs-site / mapred-site / yarn-site + hadoop-env.sh
hadoop/scripts/install_env.sh --format

# 3) 格式化 HDFS（只在首次执行；重复格式化会清空数据）
hadoop/scripts/install_env.sh --format
```

期望：`.vendor/jdk-11/` 与 `.vendor/hadoop-3.3.6/` 存在，`hadoop/conf/*.xml` 生成完毕。

---

## 2. 启动集群

```bash
hadoop/scripts/cluster.sh start     # 就地启动（不依赖 SSH，见 D-005）
hadoop/scripts/cluster.sh status    # 期望 5 个守护进程全部 UP
jps                                 # NameNode / DataNode / SecondaryNameNode
                                    # / ResourceManager / NodeManager
```

冒烟测试（Streaming 通路是否可用）：

```bash
hadoop/scripts/smoke_test.sh        # 期望打印 SMOKE: PASS
```

---

## 3. 跑测试（不含集群）

```bash
hadoop/scripts/run_tests.sh
```

期望结尾：

```
Ran <N> tests ... OK
>>> run_tests.sh: ALL PASS
```

其中包含**全量黄金测试**（`test_golden.py`，约 2 分钟）：它会用全量 ml-1m
跑一遍本地 runner，逐项断言 §7.3 的全部数字。找不到原始数据时会 **skip**
并说明原因（不会静默通过）。

---

## 4. 一键演示（推荐先跑这个）

```bash
hadoop/scripts/demo.sh --fixture    # 60 行小样本，几秒钟出结果
hadoop/scripts/demo.sh              # 全量数据，local 后端，约 1.5 分钟
hadoop/scripts/demo.sh --cluster    # 走真实 Streaming，5–10 分钟
```

脚本按接口文档 §8 的顺序打印每一步的 JSON 信封：
`validate → schemes → start → status → result → samples → report`。

导出给前端的演示数据与汇报数字表：

```bash
hadoop/scripts/export_demo_samples.sh --n 50
# 产出 .demo/{metrics.json, cleaned_sample.jsonl, quarantine_sample.jsonl, numbers.md}
```

---

## 4.5 演示性数据清洗（不经过 Hadoop）

需要「秒级拿到干净数据」时（现场演示、前端取数、Agent 联调），
不需要起集群、不需要 40 趟作业：

```bash
python3 hadoop/tools/quick_clean.py                       # 用 ML_RAW_DIR 全量，约 90 秒
python3 hadoop/tools/quick_clean.py --sample 2000         # 只抽评分表 2000 行，维表全量，几秒
python3 hadoop/tools/quick_clean.py --out /tmp/qc         # 指定输出目录
```

- 与集群任务**同源同数**：内部直接调 `engine.pipeline.run_local`（同一引擎），
  stdout 输出与 driver 一致的 JSON 信封
- **零 Hadoop 依赖**：不碰 hdfs / yarn，纯标准库
- `--sample` 只抽评分表：三表各自抽样会破坏引用完整性（见 D-012 的说明）
- 产物与集群任务同构：`<out>/cleaned/`、`<out>/metrics/`、`<out>/quarantine/`

## 5. 集群全量运行

```bash
# 5.1 物化行号并上传原始三表；同时打包 engine.zip
hadoop/scripts/upload_raw.sh                  # 全量
hadoop/scripts/upload_raw.sh --sample 2000    # 只抽样评分表（维表仍全量，见下）

# 5.2 一次跑完清洗 + 评分 + 发布（driver 按 §5.1/§5.2 串起全部作业）
ML_FULL_RUN=1 python3 hadoop/driver/run_task.py start \
    --exec cluster --foreground --tag full

# 5.3 查结果
TID=<上一步输出的 task_id>
python3 hadoop/driver/run_task.py status --task-id "$TID"
python3 hadoop/driver/run_task.py result --task-id "$TID"
```

> **不要对三张表各自独立抽样**：X1/X2 是跨表校验，抽样评分的 UserID/MovieID
> 几乎必然不在抽样维表里，会导致全部被当作孤儿隔离、cleaned 评分表变成 0 行。
> 需要控制规模时只抽样评分表（`--sample N` 的默认语义）。

---

## 6. 对账（§7.4）

```bash
# 6.1 cleaned 三表内容哈希：集群 vs 本地
hadoop/scripts/reconcile.sh --task-id "$TID"
```

脚本会逐表比对 sha256，并打印每趟作业的 counters 与 §7.3 命中数的差异。
期望：三表哈希完全一致，`counts.quarantine.by_rule` / `dedupe` / `fix` 逐项一致。

---

## 7. 发布与版本冲突

- 发布目录：`/data/published/<data_version>/`（HDFS），**不可覆盖**。
- 若目录已存在且本次 cleaned 内容哈希与上次不一致 → 任务失败，
  错误码 `VERSION_CONFLICT`，必须升 `config/cleaning_rules.v1.json` 里的
  `data_version.id` 后重跑。
- 内容哈希一致 → 视为复用，不报错（幂等）。

```bash
hdfs dfs -ls /data/published/
```

---

## 8. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `jps` 缺守护进程 | 未启动或上次异常退出 | `hadoop/scripts/cluster.sh restart` |
| `start-dfs.sh` 报 `Permission denied (publickey)` | 无密码 SSH 未配置 | 用 `cluster.sh`（D-005） |
| 作业报 `ModuleNotFoundError: _common` | `-files` 未带上作业脚本/`_common.py` | 用 `submit_stage.sh` 提交（它已带上） |
| 交付文件每行尾部多一个 `\t` | reduce 输出分隔符未置空 | 已由 D-012 的方案规避（保留零前缀 + driver 定宽剥离） |
| 交付文件行首残留 `9\t` 之类碎片 | 零填充键前缀宽度算错 | 见 `final_prefix_len`（键间是 `'::'`，两字符） |
| 清理后的评分表某条被隔离 | 维表里没有该 UserID/MovieID | 正常（X1/X2）；用 `samples --type quarantine` 查原因 |
| `start` 报 `TASK_ALREADY_RUNNING` | 有运行中任务 | 等它结束，或查 `status`；`--force` 可绕过（不推荐） |

---

## 9. 停止与清理

```bash
hadoop/scripts/cluster.sh stop      # 停止全部守护进程
rm -rf var/ .hadoop-data/ .hadoop-logs/   # 清任务产物与 HDFS 数据（会丢数据）
```
