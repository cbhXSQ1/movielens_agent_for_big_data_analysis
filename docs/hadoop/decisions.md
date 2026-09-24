# 迭代一 Hadoop 侧 · 计划偏差记录（decisions.md）

> 依据：`docs/hadoop/plan.md` §9.4 —— "遇到计划与实际不符：先在 `docs/hadoop/decisions.md` 记录（现象/选项/决定），再继续；先提出，由用户决定"。
> 本文件为**原样记录**，不擅自改计划。每条含：现象 / 影响 / 选项 / 建议 / 决定。
> 记录时间：2026-09-24（VM 实测）

---

## D-001 原始数据实际路径与计划不符

- **计划原文（plan.md §1）**：数据准备 —— "把 Windows 上的 `ml-1m.zip` 拷入 VM，解压到 `~/data/raw/ml-1m/`（三个 `.dat` 文件）"
- **实测现象**：`~/data/` 不存在。数据实际位于
  `/home/ubuntu/movielensdata/raw/ml-1m/ml-1m/`（多一层 `ml-1m` 目录），三个文件齐全：

  | 文件 | 行数 | 字节 | 编码 |
  |---|---|---|---|
  | `ratings.dat` | 1,150,241 | 28,060,211 | ASCII |
  | `users.dat` | 6,946 | 154,374 | ASCII |
  | `movies.dat` | 4,465 | 199,463 | ISO-8859-1 |

  行数与 `agent-interface.md` §4.5 的 `counts.input`（1150241 / 6946 / 4465）**完全一致**，可确认就是同一份课程数据。
- **影响**：`hadoop/scripts/env.sh` 的数据路径默认值、`upload_raw.sh`、黄金测试的输入目录。
  不影响任何算法语义与黄金数字。
- **选项**：
  - **A（建议）**：`env.sh` 的 `ML_RAW_DIR` 默认指向真实路径
    `/home/ubuntu/movielensdata/raw/ml-1m/ml-1m`，并保留环境变量覆盖能力。
  - **B**：在 `~/data/raw/ml-1m/` 建符号链接指向真实目录（需工作区外写权限）。
  - **C**：由用户把数据移动/复制到 `~/data/raw/ml-1m/`。
- **建议**：A。路径本来就是 `env.sh` 的配置项（plan.md §3 注明 env.sh 负责"HADOOP_HOME / STREAMING_JAR / 数据路径"），集中到一处配置即可，且不引入额外副本（可省 28MB 与同步风险）。
- **决定**：✅ **用户选择 A**（2026-09-24）。
  `env.sh` 导出 `ML_RAW_DIR`，默认 `/home/ubuntu/movielensdata/raw/ml-1m/ml-1m`，可由同名环境变量覆盖；
  后续 `upload_raw.sh`、黄金测试、driver 一律读此变量，不在代码里硬编码路径。

---

## D-002 无法安装系统级 Hadoop：无 root、`/opt` 不可写

- **计划原文（plan.md §1、M0.5）**："安装 Java 11（`openjdk-11-jdk`）；安装 Hadoop 3.3.6 到 `/opt/hadoop`"
- **实测现象**：
  1. `sudo` 完全不可用 —— `sudo: The "no new privileges" flag is set`；`/proc/self/status` 中 `NoNewPrivs: 1`（由运行环境强制，**非**可申请解除的权限问题）。当前用户 `ubuntu` 虽在 `sudo` 组，但无法提权。
  2. `apt install openjdk-11-jdk` 因此不可用。
  3. `/opt` 属主为 root 且不可写（即使把文件沙箱放宽到 `danger-full-access` 仍然 `权限不够`）→ **`/opt/hadoop` 在本机不可能实现**。
- **影响**：M0.5 的安装位置与 Java 来源；`env.sh` 中的 `HADOOP_HOME` / `JAVA_HOME`。
  不影响 Hadoop 功能本身——Hadoop 支持纯用户态安装运行。
- **选项**：
  - **A（建议）**：**仓库内自包含的用户态安装**（gitignore 忽略，不进版本库）
    - JDK 11 → `<repo>/.vendor/jdk-11`（Temurin 11.0.25，已下载）
    - Hadoop 3.3.6 → `<repo>/.vendor/hadoop-3.3.6`
    - HDFS 数据/日志 → `<repo>/.hadoop-data/{name,data,tmp}`、`<repo>/.hadoop-logs`
    - 优点：完全落在当前文件沙箱允许的工作区内，**无需任何提权**，一条命令可复现，不污染 git 历史，随仓库可整体迁移。
  - **B**：安装到工作区之外（`~/hadoop-3.3.6`、`~/hadoop-data`）。
    更接近计划的"机器级安装"语义，但每次 Hadoop 命令都要逐条申请沙箱放宽，交互成本高且易失败。
  - **C**：由用户以 root 在系统上装好 Java 11 + Hadoop 3.3.6 到 `/opt/hadoop`，我只负责配置与运行（最贴合计划原文）。
- **建议**：A。伪分布式 Hadoop 运行在哪个目录不影响"数据清洗与评分必须由 Hadoop 实际执行"这一硬约束；A 的可复现性最好，且与本机权限现实相容。
- **决定**：✅ **用户选择 A**（2026-09-24）。落点如下（均被 `.gitignore` 忽略，不进版本库）：

  | 用途 | 路径 | 环境变量 |
  |---|---|---|
  | JDK 11.0.25 (Temurin) | `<repo>/.vendor/jdk-11` | `JAVA_HOME` |
  | Hadoop 3.3.6 | `<repo>/.vendor/hadoop-3.3.6` | `HADOOP_HOME` |
  | Hadoop 站点配置（4 个 XML） | `<repo>/hadoop/conf` | `HADOOP_CONF_DIR` |
  | HDFS NameNode 元数据 | `<repo>/.hadoop-data/name` | — |
  | HDFS DataNode 数据块 | `<repo>/.hadoop-data/data` | — |
  | 临时目录 | `<repo>/.hadoop-data/tmp` | — |
  | 守护进程日志 | `<repo>/.hadoop-logs` | — |

  补充说明（A 方案的实现细节，无需另行决定）：
  4 个 XML 放在**仓库内** `hadoop/conf/` 而非 vendor 树内，用 `HADOOP_CONF_DIR` 指过去。
  理由是配置文件应当**可审计、可评审、进版本库**（伪分布式标准配置本身是交付物之一），
  而 vendor 树（约 1GB 二进制）不进版本库。

---

## D-003 Java 版本：计划 Java 11，系统仅有 Java 21

- **计划原文（plan.md §1、Tech Stack）**："Java 11（`openjdk-11-jdk`）"
- **实测现象**：系统仅装 `openjdk-21-jdk`（21.0.12）；`apt-cache policy openjdk-11-jdk` 有候选包但无 root 装不了。
- **影响**：Hadoop 3.3.6 官方支持 Java 8/11；Java 21 下 Hadoop 常因反射/模块化限制报错，**不建议**用 21。
- **选项**：
  - **A（建议）**：使用已下载的 Temurin JDK 11.0.25 用户态解压到 `<repo>/.vendor/jdk-11`，`env.sh` 中 `JAVA_HOME` 指向它。版本与计划一致（Java 11），不依赖 root。
  - **B**：直接用系统 Java 21 —— **不推荐**，与计划不符且有兼容性风险。
- **建议**：A（与 D-002-A 配套）。
- **决定**：✅ **用户选择 A**（2026-09-24）。`JAVA_HOME=<repo>/.vendor/jdk-11`（Temurin 11.0.25+9），
  与计划的 Java 11 大版本一致；不使用系统 Java 21。

---

## D-004 Python 版本（仅记录，无需决定）

- 计划要求 Python 3.8+（仅标准库）；实测 Python **3.12.3**，满足要求。
- 代码仍按 3.8 兼容语法书写（不用 `match`、不用 3.9+ 泛型下标等），以符合 plan.md §10 的风险对策。

---

## D-005 集群启停：`start-dfs.sh`/`start-yarn.sh` 不可用，改为就地启动守护进程

- **计划原文（plan.md M0.5 Steps）**：`start-dfs.sh && start-yarn.sh`；`jps` 验证 5 进程
- **实测现象**：本机**未配置到 localhost 的免密 SSH**：`~/.ssh/authorized_keys` 为 0 字节空文件，
  `ssh localhost` 返回 `Permission denied (publickey,password)`。
  而 Hadoop 的 `sbin/start-dfs.sh` / `start-yarn.sh` 对 workers 列表中的每台主机都要 ssh：
  经查 `libexec/hadoop-functions.sh`，`--workers` 模式最终走 `hadoop_actual_ssh`
  （`ssh ${HADOOP_SSH_OPTS} ${worker} ...`），**没有** "localhost 就免 ssh" 的捷径分支。
  因此这两个脚本在本机必然失败。
- **修复 SSH 的代价**：需要写入 `~/.ssh/authorized_keys`，该路径在**会话工作区之外**，
  写入需额外提权；而用户在 D-002 选择的 A 方案明确要求"无需任何提权"。
- **影响**：只影响"用哪个命令拉起集群"；进程集合、端口、数据目录、`jps` 输出完全一致。
- **决定**：✅ **按 D-002-A 的自包含原则，采用就地启动**（2026-09-24，无需再次确认，
  属于 D-002-A "无需提权" 的直接推论）。新增 `hadoop/scripts/cluster.sh start|stop|restart|status`：

  ```bash
  hdfs --daemon start namenode|datanode|secondarynamenode
  yarn --daemon start resourcemanager|nodemanager
  ```

  替代 `start-dfs.sh` + `start-yarn.sh`；并额外等待 NameNode 退出安全模式后再返回。
  若后续需要恢复计划原样的 `start-dfs.sh`，只需在 VM 内执行一次
  `cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys`
  （本机已存在 `~/.ssh/id_rsa`；此操作需工作区外写权限）。

### D-005 附：另两个搭建期发现（同一主题，一并记录）

1. **自定义 `HADOOP_CONF_DIR` 必须补上游默认配置**。
   `HADOOP_CONF_DIR` 指向 `hadoop/conf` 后，该目录整体取代 `$HADOOP_HOME/etc/hadoop` 在
   classpath 上的位置，导致 ResourceManager 启动直接失败：
   `IllegalStateException: Queue configuration missing child queue names for root`（缺
   `capacity-scheduler.xml`），并伴随 `log4j.properties is not found` 告警。
   → `install_env.sh` 以**白名单**方式播种 4 个上游文件：
   `capacity-scheduler.xml`、`log4j.properties`、`configuration.xsl`、`hadoop-policy.xml`。
   刻意**不**播种上游 `hadoop-env.sh` / `yarn-env.sh` / `mapred-env.sh`，
   避免它们反过来覆盖本仓库钉死的 `JAVA_HOME` 与堆参数。

2. **`jps` 的 5 个进程**：`NameNode`、`DataNode`、`SecondaryNameNode`、`ResourceManager`、`NodeManager`，
   与 plan.md §1 验收第 1 项一致（已实测 5/5 UP）。

---

## D-006 `git push` 无可用凭据 —— 里程碑提交暂存本地

- **计划原文（plan.md §9.3）**："每完成一个里程碑：跑 `hadoop/scripts/run_tests.sh` + 全量黄金测试，
  把数字贴进提交说明，然后 `git push`"
- **实测现象**：`git push --dry-run origin main` 失败：
  `fatal: could not read Username for 'https://github.com'`。
  本机无 `credential.helper` 配置、无 `GH_TOKEN`/`GITHUB_TOKEN` 环境变量，
  remote 为 HTTPS（`https://github.com/cbhXSQ1/movielens_agent_for_big_data_analysis.git`），
  非交互环境下无法输入用户名/密码。
- **影响**：里程碑提交无法推送到远端；其余开发、测试、本地提交全部不受影响。
- **处理（当前）**：**按里程碑继续本地提交**，不阻塞开发。Git 的 push 会一次性发送所有本地领先提交，
  因此凭据到位后单条 `git push origin main` 即可补齐全部里程碑，不会丢失任何一次提交。
- **需要用户提供其一**：
  - **A**：GitHub Personal Access Token（`repo` 权限），由我配置 remote 或 `credential.helper store`；
    **注意** token 会落盘到 `.git/config`，请使用可随时吊销的细粒度 token。
  - **B**：用户在 VM 内自行执行 `git push origin main`（我保证本地提交完整、可推送）。
  - **C**：改为 SSH remote 并提供可用私钥。
- **决定**：_待用户确认_（A/B/C 任一都无需改动代码，故不阻塞后续里程碑）

---

## D-007 修复动作可能产出 ISO-8859-1 无法表示的字符（P1 的 HTML 实体）

- **计划原文（plan.md 全局约束）**："输入输出保持 ISO-8859-1 + `::` 文本格式"；
  `config/cleaning_rules.v1.json` 的处理约定 `C-ENC`："所有文件按 ISO-8859-1 读取"
- **实测现象**：P1 的两条修复策略里，`decode_html_entity` 会把
  `'And God Created Woman (Et Dieu&#8230;Créa la Femme) (1956)'`
  解成 `'And God Created Woman (Et Dieu…Créa la Femme) (1956)'`，
  其中 `…` 是 U+2026 HORIZONTAL ELLIPSIS，**ISO-8859-1 里不存在该字符**
  （它只在 CP1252/Windows-1252 的 0x85 位置有），写回时会
  `UnicodeEncodeError: 'latin-1' codec can't encode character '\u2026'`。
  实测扫描全量 movies.dat：**此类记录恰好 1 条**，就是这条 HTML 实体记录；
  另 47 条乱码修复（`decode_double_encoding`）结果均为 Latin-1 安全（'é' 等）。
- **计划为何没料到**：`reference/local_prototype_scoring.py` 只把修复结果写进
  **UTF-8** 报告文件，从未写回 ISO-8859-1，所以该冲突在原型阶段不会暴露；
  Hadoop 作业必须写回 ISO-8859-1，问题才显现。
- **影响**：仅影响这 1 条记录的标题文本内容。**不影响任何黄金数字**：
  S2（编码一致性）只要求标题不再含 `&#`，三种处置都满足，
  故 §7.3 的 before/after 分数与 P1 命中数 48 均不变。
- **选项**：
  - **A（已采用）**：对修复结果做「ISO-8859-1 可编码性归一」——
    先用语义等价的 ASCII 形式替换常见标点（`…`→`...`、`—`→`-`、`’`→`'`、`€`→`EUR` 等），
    再以 `errors="replace"` 兜底。本例得到
    `'And God Created Woman (Et Dieu...Créa la Femme) (1956)'`。
  - **B**：直接用 `errors="replace"`，该字符变 `?` →
    `'(Et Dieu?Créa la Femme)'`，信息损失更大。
  - **C**：输出改用 CP1252（能表示 `…`）——**与计划明文冲突**（C-ENC 限定 ISO-8859-1），不采用。
  - **D**：把该记录整体隔离——代价是 movies 少 1 部且 S2 不再是 100%，不可接受。
- **决定**：✅ **采用 A（默认）**。实现位置：`engine/actions.py` 的 `_LATIN1_FALLBACK`
  与 `_latin1_safe()`，并在 `apply_fix` 末尾对规则涉及的字段统一归一。
  有专门测试锁定（`test_decode_html_entity_result_is_iso8859_1_encodable`）。
  **若用户希望改为 B 或 C，只需改 `_LATIN1_FALLBACK` / 写盘编码，一行改动即可。**

---

## D-008 cleaned / quarantine 的输出路径：只读配置与 plan §6 不一致

- **plan.md §6** 的任务目录草图：
  `cleaned/{ratings,movies,users}.dat`、`quarantine/{ratings,movies,users}.dat + quarantine_summary.json`
- **`config/cleaning_rules.v1.json` 的 `outputs`（只读）**：
  `"cleaned": "cleaned/{data_version}/{table}.dat"`、
  `"quarantine": "quarantine/{data_version}/{table}.dat"`，
  即在 cleaned/quarantine 与表名之间**多一层 `data_version`**。
- **影响**：只影响路径，不影响任何数字。但它是下游（driver 发布、`result.paths`、
  agent 取数、`samples` 子命令）共同依赖的契约，必须先定死。
- **选项**：
  - **A（已采用）**：以 `config.outputs` 为准 —— 它是机器可读、且**只读不可改**的声明，
    正是「输出契约」该待的地方；plan §6 是手绘草图。
    实际落盘：`<task_dir>/cleaned/<data_version>/{table}.dat`、
    `<task_dir>/quarantine/<data_version>/{table}.dat`、
    `<task_dir>/quarantine/<data_version>/quarantine_summary.json`；
    `metadata.json` 放在**任务根目录**（plan §6 明确列在 task_dir 下，
    且它描述整个任务而非某张表）。
    `result.paths.cleaned_dir` / `quarantine_dir` / `metrics_dir` 照此填。
  - **B**：以 plan §6 为准、忽略 `outputs` —— 等于「配置里写了却不执行」，
    与「配置驱动」的总体要求冲突。
- **决定**：✅ **采用 A**。多一层 `data_version` 也让同一 task_dir 能容纳多个数据版本，
  与「发布版本不可覆盖」的思路一致。
  **若你要 B，改 `engine/pipeline.py` 里 3 行 `os.path.join` 即可。**

---

## D-009 隔离记录与「标记」的落地格式

- **问题 1：隔离记录怎么落盘？** plan §5.1 只列出字段
  （`source_file,line_no,raw_line,rule_id,stage,reason`），没定分隔方式。
  用 `::` 拼接**有歧义**：`raw_line` 本身就含 `::`，无法无歧义还原。
- **问题 2：`mark`（标记）写在哪？** plan §5.1 的 movies_residual 写「输出 cleaned（含标记）」。
  但 cleaned 三表是 `::` 文本，掺入标记会**污染下游 split 与内容哈希**，
  而内容哈希正是幂等验证与 M6 对账的依据。
- **决定**：
  - ✅ **A：隔离记录写 JSONL**（每行一个 JSON 对象，八个字段与
    `quarantine_record.fields` 顺序一致），文件名仍为
    `quarantine/<data_version>/<table>.dat`。字段齐全、可审计、无歧义，
    Streaming 作业也能直接输出。（`::` 拼接因有歧义而弃用。）
  - ✅ **标记不写入 cleaned 文件**，只进 `stats.json` 的 `rule_hits.marks` 与报告。
    理由：cleaned 三表要能被下游当纯数据消费；且 M6/M7/M8/R8/R9 全是 `mark_only`，
    「标记不删」不等于「把标记写进数据」。
- **影响**：cleaned 三表逐行字段数正确、可按 ISO-8859-1 读回，内容哈希只取决于数据本身 ——
  黄金测试与 fixture 测试均有专门断言。

---

## D-010 两处让 §7.3 数字成立的**建模选择**（比率语义）

§7.3 的 36 个指标值已全部复现。其中 16 个把配置字面照搬即可；
另 2 个必须补一条建模约定 —— 两条都不是「为了凑数」，各自修掉一个真实的建模错误。

1. **比率的分子必须落在分母总体内（分子 ⊆ 分母）。**
   S3 是唯一分子与分母都带 `where` 的比率：分子「非毫秒时间戳」、分母「可解析时间戳」。
   若分子直接对**全部**解析记录计数，非整数时间戳（3,375 条 `'five'`）会因为
   `timestamp_unit_ms` 对非整数返回 False 而被算进分子、却不在分母里 →
   **S3 = 99.11%，且比率可能 > 1**（若全部整数时间戳都是毫秒）。
   把分子的 `where` 与分母的 `where` 取合并 → **98.81%，与黄金值一致**。
   实现：`engine/metrics.py::_numerator_spec()`；
   测试：`test_metrics.TestRatioNumeratorIsSubsetOfDenominator`。
2. **U2 的分母是原始行数，其中「无法解析的行」要算作不重复行。**
   U2 问的是「有多少行不是别人的冗余副本」。raw 侧有 13,681 行（P2/P3 隔离）
   根本无法解析，它们不可能是任何已解析记录的副本。
   若只统计解析记录的去重结果 → **91.32%**；
   等价于参考原型的 `U2 = 1 - Σ(n_t - distinct_t) / raw_lines`
   （其 `extra` 只统计解析记录**内部**的重复），即「distinct(解析) + 未解析行数」
   → **92.50%，与黄金值一致**。
   实现：`engine/metrics.py::_agg_distinct_row_count()`；
   测试：`test_metrics.TestUnparsedLinesCountAsUniqueRows`。
   cleaned 侧 `raw_lines == len(parsed)`，该项自然为 0，故 after 侧仍是 100%。

- 附带约定：**分母为 0 记满分**（与参考原型一致）—— 空数据集不应因「没有记录可判」而扣分。
- **决定**：✅ 采用上述约定，并在函数 docstring 与单元测试中就地锁定。

---

## D-011 只读配置里 `evidence` 的实测值与其 `detect` 声明不一致（4 处）

处置行为完全由 `detect`（机器可读）驱动；但配置内 `evidence` 字段与
`cleaning_rules.v1.说明.md` 给的「实测命中数」有 4 处与其**自身 `detect`** 对不上。
**这 4 处都不影响任何契约数字**：§7.3 的 counts、`agent-interface.md §4.5` 的 counts
与 36 个指标值全部逐项复现，只影响报告里的**描述性统计**。

| 规则 | 配置/说明里的实测值 | 按 `detect` 声明的真实值 | 原因 |
|---|---|---|---|
| U2 | `records: 123`、`field_hits: 369` | **369 条记录**、369 处字段 | `123` 是**每个字段**各自的非法条数（Gender/Age/Occupation 各 123）；三个字段的非法记录集互不重叠，故记录数也是 369。其中 69 条（每字段 23 条）本来就是空值，`blank_invalid_field` 无可清空，故实际清空 300 处 |
| U3 | `measured_hits: 202`（73 截取 + 129 置空） | **201 条**（73 ZIP+4 + 106 置空 + 22 本就空） | 差 1 条；22 条空邮编会被 `regex_mismatch` 命中，但没有可清空的内容 |
| M8 | `groups: 218` | 清洗后 **1 组**（raw 上按不同 MovieID 计为 61 组） | `218` 只在「**raw** 数据上、同一标题出现 ≥2 **行**」时成立；按配置声明的 `key: MovieID, min_keys: 2` 语义应为「≥2 个不同 MovieID」。清洗后只剩 1 组（`'Léon / Amélie (1994)'`，25 个 MovieID —— 正是注入的 47 条乱码副本被 P1 还原、又分属不同 MovieID 的结果） |
| R9 | `matched_users: 30` | 按 `min_matches: 1000` 命中 **17 人** | 恰好 UserID 1–30 共 30 人有非法评分（合计 43,885 条），但分布不均（每人 226–4,353 条），仅 17 人 ≥1000。证据里的「30 人」对应的是「有任何非法评分」这个更宽的口径 |

- **决定**：✅ **一律以 `detect` 的机器可读声明为准** —— 它是配置里真正被执行的部分，
  也是唯一能保证「本地 = 集群」的口径。`stats.json` 给出按 `detect` 得到的真实值，
  报告（RPT-HITS）同时附上本表，把差异讲清楚，而不是引用不可复现的 evidence 数字。
- **需要你确认**：若你希望这 4 处改按 evidence 口径（例如 M8 改成「raw 上同名行数」），
  那属于**改清洗语义**，要动 `detect`/引擎并牵动报告数字。
  当前实现严格遵循配置，未做此改动。

---

## 决策汇总

| 编号 | 问题 | 处理 |
|---|---|---|
| D-001 | 数据路径与计划不符 | ✅ 用户选 A：`ML_RAW_DIR` 指向真实路径 |
| D-002 | 无 root、`/opt` 不可写 | ✅ 用户选 A：仓库内自包含用户态安装 |
| D-003 | 系统只有 Java 21 | ✅ 用户选 A：用户态 Temurin JDK 11 |
| D-004 | Python 3.12 vs 计划 3.8+ | 记录即可，满足要求 |
| D-005 | `start-dfs.sh` 需 SSH | 按 D-002-A 推论改为就地启动（`cluster.sh`） |
| D-006 | `git push` 无凭据 | ⏳ 待用户提供 token / 自行推送（不阻塞开发） |
| D-007 | P1 修复产出非 Latin-1 字符（1 条） | ✅ 采用 A：ASCII 归一 + replace 兜底；不影响黄金数字 |
| D-008 | cleaned/quarantine 路径：配置 `outputs` vs plan §6 | ✅ 采用 A：以只读配置 `outputs` 为准（多一层 data_version） |
| D-009 | 隔离记录与标记怎么落盘 | ✅ 隔离记录 JSONL；标记不进 cleaned 文件，只进 stats/报告 |
| D-010 | 比率语义：分子 ⊆ 分母；未解析行算不重复行 | ✅ 采用，使 S3=98.81%、U2=92.50% 与黄金一致；有专门测试 |
| D-011 | 配置 `evidence` 实测值与自身 `detect` 不一致（U2/U3/M8/R9） | ✅ 以 `detect` 为准（不影响任何契约数字）；⏳ 待你确认是否改语义 |

> 后续如再遇计划与实际不符，按同一格式**追加** D-012、D-013…，不覆盖本文件已有记录。
