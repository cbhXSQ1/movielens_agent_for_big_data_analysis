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

> 后续如再遇计划与实际不符，按同一格式**追加** D-006、D-007…，不覆盖本文件已有记录。
