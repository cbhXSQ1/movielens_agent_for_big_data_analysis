# MovieLens 1M 数据分析 Agent 系统

组队序号 47 ｜ 组名：誓死相信软工
成员：谢苏秦（第 4 节，清洗与评分）、李丞渊（第 5 节，Agent）、卢忠源（第 6 节，前端）
代码仓库：https://github.com/cbhXSQ1/movielens_agent_for_big_data_analysis

用户在前端用自然语言提问，Agent（**LLM 主控** 或 **离线直调**）把意图落成对远端能力服务的调用，由 Hadoop 真实执行数据清洗与五维质量评分；结果带版本、可追溯、不编造。本仓库附有一次真实运行的产物，见 `评估产物/`。

## 一、系统形态（迭代一重构后的架构）

```
宿主机：前端（Vue 3）── HTTP + 每对话一条 SSE ──► Agent（Python）
                                                    │  标准 MCP over HTTP（入口 /mcp，带口令）
                                                    ▼
虚拟机：能力服务（mcpserver）── 调用 ──► Hadoop 集群（driver + 作业链 + HDFS）
```

- **Agent 跑在宿主机**，两种模式：`LLM 主控`（模型多步选择并调用工具、流式输出）与 `离线直调`（不依赖模型，按工具表填参直接调用）。
- **能力服务跑在虚拟机**：把既有的校验、清洗、评分、发布、查询产物等能力，以标准 **MCP**（工具 + 只读资源）对外；契约版本 `1.0`。
- **鉴权**：除健康检查 `/health` 外，一律需要请求头 `Authorization: Bearer <口令>`；口令只留在后端，不写日志、不下发给浏览器。
- **长任务**：异步受理，立即返回任务编号；进度由 Agent 侧后台**定时查状态**获得（远端不做主动推送），完成后再唤醒模型续写。
- **网络**：虚拟机为默认地址转换模式，宿主机**不能主动连入**，必须在虚拟机软件里加一条**端口转发**（宿主机 `8900` → 虚拟机 `8765`）。

> **命名**：本仓库这一轮（能力服务 + Agent 重构 + 前端重写）统称 **迭代一重构**；课程口径的 **迭代二（机器学习：分类/聚类/降维）** 是其后续阶段，尚未开始。

## 二、目录结构

| 路径 | 内容 |
|---|---|
| `hadoop/` | 清洗与评分引擎、Streaming 作业、driver（任务编排、发布与版本保护）、**能力服务 `mcpserver/`**、运维脚本、测试 |
| `agent/` | Agent：LLM 主控运行时、离线直调、HTTP 服务、统一工具表、**远端能力服务客户端与下载中转** |
| `frontend/` | 数据治理控制台：Vue 3 + Vite + TypeScript + Naive UI + Pinia（有构建、有测试） |
| `config/` | 清洗方案 `cleaning_rules.v1.json` 与评分方案 `scoring_scheme.v1.json`（带版本号，只读） |
| `docs/` | 三组文档：系统说明、运行方法、接口契约、端侧要求、决策记录 |
| `start_backend.bat` | 宿主机一键启动 Agent 后端（Windows） |
| `requirements.txt` | Agent 后端依赖（LangGraph、官方 MCP 客户端、httpx 等） |
| `reference/`、`评估产物/` | 本地原型与一次真实运行的产物 |

## 三、环境要求

| 端 | 要求 |
|---|---|
| 宿主机 | Windows；Python 3.10+（依赖见 `requirements.txt`）；Node.js 20+（前端构建） |
| 虚拟机 | Ubuntu 22.04；Hadoop 3.3.6 伪分布式（NameNode、DataNode、SecondaryNameNode、ResourceManager、NodeManager、JobHistoryServer）；Java 11；Python 3.8+ |
| 数据 | MovieLens 1M 三个文件（ISO-8859-1、`::` 分隔、无表头），放在虚拟机的原始区 |
| 网络 | 虚拟机软件里加端口转发：宿主机 `8900` → 虚拟机 `8765` |

## 四、快速开始

### 第 1 步 虚拟机：起集群与能力服务

```bash
cd ~/movieLens-agent
hadoop/scripts/cluster.sh start
hadoop/scripts/cluster.sh status          # 期望 running=6/6
hadoop/scripts/mcp_server.sh init         # 首次：生成配置与随机访问口令（幂等，已存在则保留）
hadoop/scripts/mcp_server.sh start        # 启动能力服务（监听 0.0.0.0:8765）
hadoop/scripts/mcp_server.sh token        # 打印访问口令，复制到宿主机用
```

### 第 2 步 虚拟机软件：加端口转发

虚拟机设置 → 网络 → 网卡（NAT）→ 高级 → **端口转发** → 添加：协议 `TCP`、主机端口 `8900`、子系统端口 `8765`。

### 第 3 步 宿主机：启动 Agent 后端

先设置两个环境变量，再运行 `start_backend.bat`（等价的直接启动是 `python -m agent.http_api --port 8765`）：

```
set AGENT_MCP_URL=http://127.0.0.1:8900/mcp
set AGENT_MCP_TOKEN=<第 1 步取到的口令>
```

后端起在 `http://127.0.0.1:8765`。不设这两个变量后端也能起，只是远端能力会报"未接入"。`/health` 会报告远端连接状态（口令只显示"是否已设置"，不回显）。LLM 的连接参数建议在前端"设置"面板里填，或作为后端默认值用环境变量给。

### 第 4 步 宿主机：启动前端

```bash
cd frontend
npm install
npm run dev
```

打开 dev 提示的地址即可。

### 第 5 步 在浏览器里发起任务

新建对话，选 `LLM 主控` 或 `离线直调`，输入需求（示例）：

> 请使用默认规则清洗 MovieLens 1M，评估清洗前后的 Accurate、Complete、Unique、Up-to-date、Consistent 五个维度，并说明处理了哪些问题、还有哪些无法解决。

长任务在左栏"任务进度"里看：同时显示 Agent 编号与 Hadoop 编号、阶段与百分比；完成后会续写一条回复，产物以"标签"形式出现在中栏。

## 五、命令行（可选）

在**虚拟机**上直接调用 driver（工具接口的原始口径见 `docs/hadoop/agent-interface.md`）：

```bash
python3 hadoop/driver/run_task.py validate
python3 hadoop/driver/run_task.py start --scope full
python3 hadoop/driver/run_task.py status  --task-id <任务号>
python3 hadoop/driver/run_task.py result  --task-id <任务号>
python3 hadoop/driver/run_task.py samples --task-id <任务号> --type quarantine --table ratings --n 5
python3 hadoop/driver/run_task.py report  --task-id <任务号> --format md
python3 hadoop/driver/run_task.py tasks
python3 hadoop/driver/run_task.py schemes
```

另有 Agent 侧的本地直取命令行，用于在能访问 driver 的机器上排障：`python -m agent.cli <子命令> --explain`（子命令：`validate` / `schemes` / `start` / `status` / `result` / `samples` / `report` / `tasks` / `quick-clean`）。正式使用请走 Web 界面。

同一时刻只允许一个运行中的任务；正式结果以集群执行为准。附加的 `quick-clean`（同引擎、不经 Hadoop、不发布）仅用于现场快速取数。

## 六、接口一览（前端 ↔ Agent）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 服务、工具来源、远端连接与 LLM 增强层状态 |
| POST / GET | `/api/conversations` | 新建 / 列出对话 |
| DELETE | `/api/conversations/{cid}` | 删除对话 |
| GET | `/api/conversations/{cid}/stream` | 打开该对话的推送流（SSE） |
| POST | `/api/conversations/{cid}/messages` | 发消息（按 `mode` 分派 `offline` / `llm`） |
| POST | `/api/conversations/{cid}/context` | 设置上下文（任务 / 数据版本） |
| GET | `/api/conversations/{cid}/tree` | 读整棵对话树 |
| GET | `/api/conversations/{cid}/nodes/{node_id}` | 读单个节点 |
| POST | `/api/conversations/{cid}/branches` | 从某节点另起一条支 |
| GET | `/api/conversations/{cid}/artifacts` | 恢复产物列表 |
| GET | `/api/conversations/{cid}/messages` | 消息历史 |
| GET | `/api/conversations/{cid}/tasks` 与 `…/{agent_task_id}` | 长任务进展 / 单个长任务 |
| GET | `/api/conversations/{cid}/tasks/{agent_task_id}/result` | 结构化结果板块 |
| GET | `/api/tools` | 工具表只读兜底 |
| GET | `/api/data/raw` | 列原始数据（远端只读） |
| GET | `/api/data/published` | 列已发布数据版本 |
| GET | `/api/schemes` 与 `/api/schemes/content?name=` | 列方案 / 取方案内容（远端） |
| POST | `/api/schemes/upload` | 上传自定义方案（经远端能力服务落盘，先校验、后写入） |
| GET | `/api/tasks/{tid}/files` | 列任务产出文件（`tid` 为 Hadoop 任务编号） |
| GET | `/api/files/preview?task_id=&path=` | 预览文本产物 |
| GET | `/api/files/download?task_id=&path=` | 下载任务产物（Agent 带口令中转） |
| GET | `/api/data/published/download?data_version=&table=` | 下载已发布数据表 |
| POST | `/api/chat`、`/api/validate`、`/api/llm/test` | 自然语言入口 / 方案校验 / 大模型连接测试 |

失败一律返回结构化信封 `{"ok": false, "error": {"code", "message"}}`，错误码两端统一。原始数据上传不对外提供（远端不接收），前端不再有上传入口。

## 七、能力服务对外能力（Agent ↔ Hadoop）

- **工具（23 项）**：校验方案、列方案、发起全流程、只清洗、独立评分、查状态、取结果、取样例、取报告、列任务；取证与自描述（列已发布版本、列任务产物、读产物、取作业计数、取作业日志、列原始数据、取方案内容、能力自描述）；补充（等任务结束、取消任务、查执行位、探活）；**上传方案**（先校验后写入，同名默认拒绝、默认方案不可覆盖）。
- **只读资源**：`ml://schemes`、`ml://scheme/{name}`、`ml://published`、`ml://tasks`、`ml://task/{id}/status|files|report|quarantine`。
- **长任务**：异步受理 + 等待或查询；状态取值 `queued / running / succeeded / failed / aborted`。
- **两段下载路由**：`/download/task/{任务编号}/{相对路径}`、`/download/published/{数据版本}/{表名}`（同一口令）。
- 契约版本两端同为 `1.0`，只做加性变更。细节见 `docs/hadoop/迭代一重构_MCP端侧开发要求.md`、`docs/hadoop/迭代一重构_对端改造说明.md`、`docs/agent/Agent_接口文档.md`（§5）。

## 八、运行产物

每次任务在**能力服务所在的机器**上产出（`var/tasks/<任务号>/`）：

| 文件或目录 | 内容 |
|---|---|
| status.json | 状态、当前阶段、进度百分比、错误信息 |
| result.json | 权威结果信封（版本、T1/T2、计数、五维、指标） |
| report.md / report.json | 评估报告：数据量变化、规则命中、五维评分、评价局限、说明 |
| counts.json / stats.json | 数据量与规则命中统计 |
| metrics/ | 清洗前、清洗后、综合分三份指标 |
| cleaned/<数据版本>/ | 清洗后的三个数据文件 |
| quarantine/<数据版本>/ | 隔离区：原始行、命中规则、原因、行号 |
| logs/ | 作业与驱动日志（结果页默认折叠） |

集群模式还会把清洗结果发布到 HDFS：`/data/published/ml1m-clean-v1/`，附内容哈希清单；同一版本再次发布时哈希不一致会直接报版本冲突，必须升版本，不允许静默覆盖。

`评估产物/` 另附一次真实运行的结果，详细说明见该目录内 `说明.md`。

## 九、版本与时间边界

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

## 十、测试与自检

- 后端（本机、不联网）：`python -m pytest agent/tests`
- 前端：`cd frontend && npm run test`，构建 `npm run build`
- 能力服务自检（虚拟机）：`hadoop/scripts/mcp_server.sh status`（在不在跑、监听在哪、口令在不在）；参考客户端 `hadoop/tools/mcp_client.py list` 可列出全部能力与参数（用 `--token-file` 给口令，见 `docs/hadoop/runbook.md` §9）。

## 十一、文档索引（对应课程要求）

| 要求 | 文档 |
|---|---|
| 系统说明 | `docs/agent/第5节_Agent系统设计说明.md`、`docs/frontend/系统说明.md`、`docs/hadoop/hadoop-primer.md`、`docs/hadoop/plan.md` |
| 运行方法 | `docs/hadoop/runbook.md`、`docs/agent/虚拟机联调_傻瓜步骤.md`、`docs/agent/环境搭建_Ubuntu虚拟机.md` |
| 接口契约 | `docs/agent/Agent_接口文档.md`（前端↔Agent、Agent↔Hadoop）、`docs/hadoop/agent-interface.md`（driver 命令行）、`docs/hadoop/迭代一重构_MCP端侧开发要求.md`、`docs/hadoop/迭代一重构_对端改造说明.md` |
| 数据与模型版本 | `docs/hadoop/iteration-1-results.md`（本轮无模型产物，沿用迭代一数据版本与 T1/T2） |
| 实验配置 | `config/` 两份方案及说明文档 |
| 评价结果 | `docs/hadoop/iteration-1-results.md`、`评估产物/评估报告.md` |
| 已知限制 | 评估报告与 `迭代一交付说明.md` 中的局限章节 |

## 十二、已知限制

1. 目前是单机伪分布式部署，没有做多机验证。
2. 宿主机与虚拟机走默认地址转换模式，**必须手工加一条端口转发**（宿主机 `8900` → 虚拟机 `8765`）宿主机才能连上能力服务。
3. **自定义方案上传已打通**：前端上传 → Agent 带口令转发 → 远端能力服务落盘（先校验、后写入；同名默认拒绝，需显式允许覆盖；两份随仓库交付的默认方案不可覆盖）。
4. 用户属性为自愿填写、未经核验，所以准确性与完整性不封顶，这是有意为之。
5. 分数提升有一部分来自把无法判定的记录移出分母，不等于这些问题被修复，报告里同屏披露。

## 十三、数据来源

MovieLens 1M 数据集由明尼苏达大学 GroupLens Research 发布，仅用于本课程项目。
