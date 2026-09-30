# MovieLens 1M 数据分析 Agent 系统（迭代一）

组队序号 47 ｜ 组名：誓死相信软工
成员：谢苏秦（第 4 节，清洗与评分）、李丞渊（第 5 节，Agent）、卢忠源（第 6 节，前端）

用户在前端输入自然语言请求，Agent 组织任务，Hadoop 真实执行数据清洗与五维质量评分；结果带版本、可追溯、不编造。本压缩包附有一次真实运行的产物，见 `评估产物/`。

## 一、目录结构

| 路径 | 内容 |
|---|---|
| `hadoop/` | 清洗与评分引擎、Streaming 作业、driver（任务编排、发布与版本保护）、运维脚本、测试 |
| `agent/` | Agent 工具、意图解析、HTTP 服务（11 个端点）、命令行入口 |
| `frontend/` | 数据治理控制台：原生 HTML/CSS/JS，零依赖、零构建，图表为手写 SVG |
| `config/` | 清洗方案 `cleaning_rules.v1.json` 与评分方案 `scoring_scheme.v1.json`（带版本号，只读） |
| `docs/` | 三组文档：系统说明、运行方法、工具接口、评价结果、决策记录 |
| `reference/` | 本地原型与实测输出（结果对账的语义依据） |
| `评估产物/` | 本次提交附带的真实运行产物：评估报告、指标、样例、清洗后数据 |
| `迭代一交付说明.md` | 提交内容总览与关键结果 |

## 二、环境要求

| 项 | 要求 |
|---|---|
| 系统 | Ubuntu 22.04 虚拟机（VMware；建议以管理员身份启动） |
| Hadoop | 3.3.6 伪分布式，六个进程：NameNode、DataNode、SecondaryNameNode、ResourceManager、NodeManager、JobHistoryServer |
| Java | OpenJDK 11 |
| Python | 3.8 及以上；仅标准库，无第三方依赖 |
| 数据 | MovieLens 1M 三个文件（ISO-8859-1 编码、`::` 分隔、无表头），放在 `~/data/raw/ml-1m/` |

## 三、快速开始（五步）

### 第 1 步 启动 Hadoop 集群（虚拟机每次开机后必须做）

```bash
cd ~/movieLens-agent
hadoop/scripts/cluster.sh start
hadoop/scripts/cluster.sh status        # 期望输出 running=6/6
```

### 第 2 步 启动 Agent 后端

逐行执行下面八行（缺一行都可能让任务半路失败）：

```bash
export JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
export HADOOP_HOME=/opt/hadoop
export HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop
export STREAMING_JAR=/opt/hadoop/share/hadoop/tools/lib/hadoop-streaming-3.3.6.jar
export ML_RAW_DIR=/home/hadoop/data/raw/ml-1m
export AGENT_LLM_SUPPORTED=1
cd ~/movieLens-agent
python3 -m agent.http_api --port 8765
```

看到"Agent HTTP API 已启动"后保持运行，不要关闭这个终端。最后两行里的 `AGENT_LLM_SUPPORTED` 是可选的大模型增强开关，不设置也不影响使用（系统自动走规则解析）。

### 第 3 步 启动前端页面服务

另开一个终端：

```bash
cd ~/movieLens-agent
python3 -m http.server 8000
```

### 第 4 步 在浏览器里发起任务

用虚拟机内的浏览器打开：`http://localhost:8000/frontend/index.html`，右上角应显示"后端已连接"。

在主输入框输入自然语言需求（示例）：

> 请使用默认规则清洗 MovieLens 1M，评估清洗前后的 Accurate、Complete、Unique、Up-to-date、Consistent 五个维度，并说明处理了哪些问题、还有哪些问题无法解决。

选择执行方式后点击发送：

- Hadoop 集群：正式口径，全量约 8 至 10 分钟，期间可查看九个阶段的进度；
- 本地引擎：同一套引擎但不经 Hadoop，秒级出结果，仅供快速演示。

### 第 5 步 查看结果与追问

任务成功后，右侧五个视图展示：五维对比、评分依据、清洗结果、样例与报告、总览。左栏"追问 Agent"支持的自然说法示例：

- 任务 <任务号> 跑到哪一步了
- 为什么要隔离这些行
- 解释为什么这么评分
- 给我看 5 条被隔离的评分记录
- 出一份完整评估报告
- 现在有哪些已登记的清洗方案

## 四、命令行用法（可选）

直接调用 driver：

```bash
python3 hadoop/driver/run_task.py validate                     # 校验配置
python3 hadoop/driver/run_task.py start                        # 发起任务（异步；默认全量、集群执行）
python3 hadoop/driver/run_task.py status  --task-id <任务号>
python3 hadoop/driver/run_task.py result  --task-id <任务号>
python3 hadoop/driver/run_task.py samples --task-id <任务号> --type quarantine --table ratings --n 5
python3 hadoop/driver/run_task.py report  --task-id <任务号> --format md
python3 hadoop/driver/run_task.py tasks
python3 hadoop/driver/run_task.py schemes
```

或经 Agent 命令行（会附带中文解释）：

```bash
python3 -m agent.cli start  --exec cluster --foreground --tag demo
python3 -m agent.cli result --task-id <任务号> --explain
```

说明：同一时刻只允许一个运行中的任务；正式结果以集群执行为准。另有一个附加演示工具 `quick_clean`（同引擎、不经 Hadoop、不发布），仅用于现场快速取数，权威结果仍以 `start` 加 `result` 为准。

## 五、HTTP 接口（前端对接用）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /health | 服务与大模型增强层状态 |
| GET | /api/schemes | 已登记方案列表 |
| GET | /api/tasks | 任务列表 |
| GET | /api/tasks/{id}/status | 任务进度 |
| GET | /api/tasks/{id}/result | 任务结果 |
| GET | /api/tasks/{id}/samples | 样例（cleaned 或 quarantine） |
| GET | /api/tasks/{id}/report | 评估报告 |
| POST | /api/tasks | 发起任务 |
| POST | /api/chat | 自然语言入口 |
| POST | /api/validate | 配置校验 |
| POST | /api/llm/test | 大模型连接测试 |

## 六、运行产物

每次任务在 `var/tasks/<任务号>/` 下产出一整套结果：

| 文件或目录 | 内容 |
|---|---|
| status.json | 状态、当前阶段、进度百分比、错误信息 |
| result.json | 权威结果信封（版本、T1/T2、计数、五维、18 项指标） |
| report.md / report.json | 评估报告：数据量变化、规则命中、五维评分、评价局限、口径说明 |
| counts.json / stats.json | 数据量与规则命中统计 |
| metrics/ | 清洗前、清洗后、综合分三份指标 |
| cleaned/<数据版本>/ | 清洗后的三个数据文件 |
| quarantine/<数据版本>/ | 隔离区：原始行、命中规则、原因、行号 |

集群模式还会把清洗结果发布到 HDFS：`/data/published/ml1m-clean-v1/`，附内容哈希清单；同一版本再次发布时哈希不一致会直接报版本冲突，必须升版本，不允许静默覆盖。

本次提交另附一份真实运行的产物在 `评估产物/` 目录，详细说明见该目录内的 `说明.md`。

## 七、版本与时间边界

| 项 | 值 |
|---|---|
| 数据版本 | ml1m-clean-v1 |
| 清洗规则版本 | cleaning_rules 1.0.0（sha256 记录在任务元数据） |
| 评分方案版本 | scoring_scheme 1.0.0（sha256 记录在任务元数据） |
| 训练期截止 T1 | 2001-12-31 23:59:59 UTC（时间戳小于等于 T1 的数据用于训练） |
| 验证期截止 T2 | 2002-06-30 23:59:59 UTC（T1 与 T2 之间为验证期，T2 之后为测试期） |

本次运行关键数字（详见 `docs/hadoop/iteration-1-results.md` 与 `评估产物/`）：

- 输入评分 1,150,241 行，输出 1,000,209 行；隔离 100,830 行；去重 49,510 行；修复 13,503 行；
- 五维（清洗前 → 清洗后）：准确性 97.71 → 99.79，完整性 98.82 → 99.99，唯一性 90.90 → 100，时效性 98.22 → 100，一致性 89.65 → 100；综合分 95.07 → 99.95。

## 八、文档索引（对应课程要求）

| 要求 | 文档 |
|---|---|
| 系统说明 | `docs/agent/第5节_Agent系统设计说明.md`、`docs/frontend/系统说明.md`、`docs/hadoop/hadoop-primer.md`、`docs/hadoop/plan.md` |
| 运行方法 | `docs/hadoop/runbook.md`、`docs/agent/虚拟机联调_傻瓜步骤.md`、`docs/agent/环境搭建_Ubuntu虚拟机.md` |
| 工具接口 | `docs/hadoop/agent-interface.md`（命令行契约）、`docs/agent/前端对接接口.md`（HTTP 接口） |
| 数据与模型版本 | `docs/hadoop/iteration-1-results.md`（本轮无模型产物，迭代二产出并沿用本轮数据版本与 T1/T2） |
| 实验配置 | `config/` 两份方案及说明文档 |
| 评价结果 | `docs/hadoop/iteration-1-results.md`、`评估产物/评估报告.md` |
| 已知限制 | 评估报告与 `迭代一交付说明.md` 中的局限章节 |

## 九、已知限制

1. 当前大模型仅作为意图增强（可选开启、单步），系统最优先的逻辑是规则匹配；迭代二将把 Agent 改造成 LLM 主控的 function-calling 循环（支持多步工具编排），规则退为兜底与护栏。
2. 自定义清洗与评分方案的接口已经支持，前端编辑入口还没有做。
3. 用户属性为自愿填写、未经核验，所以准确性与完整性不封顶，这是有意为之。
4. 分数提升有一部分来自把无法判定的记录移出分母，不等于这些问题被修复，报告里同屏披露。
5. 目前是单机伪分布式部署，没有做多机验证。

## 十、数据来源

MovieLens 1M 数据集由明尼苏达大学 GroupLens Research 发布，仅用于本课程项目。
