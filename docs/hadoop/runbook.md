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
不需要起集群（D-014 后全量集群链路为 15 趟、约 20~30 分钟）：

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

## 4.6 LLM 可选层联调（连接参数全部由前端提供）

架构口径（2026-09-29 与组长定稿）：**后端不持有任何"默认 API 连接"**。
后端只声明能力和总开关；端点 / key / 模型 / 模式全部由**前端设置面板**
在请求时传入（`body.llm`，优先级最高，不落盘）：

```json
// config/llm.settings.json（可选；已 gitignore，不入库）只允许这两种键：
{ "supported": true, "enabled": true }
// api_base / api_key / model / mode 一律由前端面板在 /api/chat、/api/llm/test
// 的 body.llm 里给出；后端 load() 会强制剔除请求里的 "supported"（总开关仅后端可开）
```

- `/health` 语义：`supported` = 后端总开关（AGENT_LLM_SUPPORTED 或配置文件的
  supported）；`configured` = 本次请求（含 body.llm）是否配齐连接 ——
  不带 body 时应为 `configured: false`，这不是故障；
- `/api/llm/test` 不带 body → `LLM_NOT_CONFIGURED`（缺 api_base/key/model）；
  带 body → 按面板配置实测可达性；
- opencode Zen/Go 网关（`https://opencode.ai/zen/go/v1`）需要
  `x-opencode-session` 头，且 Cloudflare 按 TLS 指纹封禁 python-urllib
  （403 code 1010）。**无需单独起适配器**：Agent 按 `api_base` 的 URL 自行分派
  —— 面板 api_base 直接填 `https://opencode.ai/zen/go/v1`，Agent 识别出
  opencode 网关后进程内用 curl 转发并补会话头（`agent/gateway/curl_gateway.py`），
  其余标准 OpenAI 兼容端点（如 DeepSeek 官方）走普通 HTTP，无需任何额外配置。

行为口径（agent 侧已写死，答辩别讲错）：
- `mode=fallback`：规则识别为 unknown 才兜底问大模型 —— 绝大多数
  交互零延迟零成本，行为确定可复现；**agent 代码的默认值**；
- `mode=always`：每句都问 —— LLM 自主理解需求，规则只在它输出不合法时兜底。
  实测代价：单句 1~30s（网关波动）、白名单内输出跨时刻可能不一致
  （网关路由漂移，见 agent-review-llm-defects.md L9）、元问题仍判 unknown；
- 数字永远来自 driver 返回；LLM 只见占位符、回来过四道校验。

## 5. 集群全量运行

```bash
# 5.1 物化行号并上传原始三表；同时打包 engine.zip
hadoop/scripts/upload_raw.sh                  # 全量
hadoop/scripts/upload_raw.sh --sample 2000    # 只抽样评分表（维表仍全量，见下）

# 5.2 一次跑完清洗 + 评分 + 发布（driver 按 §5.1/§5.2 串起全部作业）
#     D-016：默认就是全量（正式口径，约 8 分钟）；显式 --scope sample 才抽样本
python3 hadoop/driver/run_task.py start \
    --exec cluster --foreground --tag full          # 全量（默认）
# python3 ... start --exec cluster --scope sample   # 样本（仅联调）

# 5.3 查结果
TID=<上一步输出的 task_id>
python3 hadoop/driver/run_task.py status --task-id "$TID"
python3 hadoop/driver/run_task.py result --task-id "$TID"
```

> **不要对三张表各自独立抽样**：X1/X2 是跨表校验，抽样评分的 UserID/MovieID
> 几乎必然不在抽样维表里，会导致全部被当作孤儿隔离、cleaned 评分表变成 0 行。
> 需要控制规模时只抽样评分表（`--sample N` 的默认语义）。
>
> 旧的环境变量 `ML_FULL_RUN=1` 已无意义（现在默认就是全量），保留只是兼容旧命令。

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

## 9. 能力服务（迭代二 · 对宿主机 Agent 开放）

> 目的：把上面这些数据治理能力以标准协议形式对宿主机上的 Agent 开放。
> 设计与对外能力清单见 `docs/hadoop/迭代二_MCP_实现方案.md`；
> 实现要求见 `docs/hadoop/迭代二_MCP端侧开发要求.md`。

### 9.1 一次性准备

```bash
# 仓库内独立环境（官方协议实现包装在这里，系统解释器不受影响）
python3 -m venv --without-pip .venv
curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && .venv/bin/python /tmp/get-pip.py
.venv/bin/python -m pip install mcp

# 生成配置与随机访问口令（幂等：已有配置就保留，不会悄悄换掉正在用的口令）
hadoop/scripts/mcp_server.sh init
# 换口令：换一枚新的，其余配置与调优项原样保留；旧口令立即失效，需要同步更新对端
hadoop/scripts/mcp_server.sh init --force
```

配置与口令都在 `var/mcp/server.json`（权限 600，**不入版本库**）。

### 9.2 启动与自检

```bash
hadoop/scripts/mcp_server.sh start     # 以独立会话常驻，脱离启动它的终端
hadoop/scripts/mcp_server.sh status    # 在不在跑、监听在哪、口令指纹、允许的 Host
hadoop/scripts/mcp_server.sh token     # 打印口令（人工复制到宿主机侧）
hadoop/scripts/mcp_server.sh logs -f   # 跟踪日志（口令只留前四位指纹）
hadoop/scripts/mcp_server.sh stop      # 停止
```

默认监听 `0.0.0.0:8765`，协议入口 `/mcp`，健康检查 `/health`（健康检查不需要口令）。

### 9.3 宿主机侧要做的两件事

1. **加一条端口转发**：虚拟机软件里把宿主机本机的一个端口转到虚拟机 `8765`。
   本机走默认地址转换模式，宿主机不能主动连进来，**不加转发则连不上**（这是实测结论）。
   两边端口号**可以不同**：宿主机侧用什么号由你定（本项目联调时用的是宿主机 `8900`），
   虚拟机侧始终是 `8765`。宿主机连 `http://127.0.0.1:<你定的号>/mcp` 即可 ——
   服务放行`127.0.0.1` 的任意端口，不必跟着改配置。
2. **带上口令**：请求头 `Authorization: Bearer <口令>`。缺口令会被拒（401），不会静默放行。

### 9.4 用仓库自带的客户端核验（对端也可照此对接）

```bash
# 不带口令：应看到 401 与明确的错误信封
.venv/bin/python hadoop/tools/mcp_client.py authcheck --url http://127.0.0.1:8765/mcp

# 带口令：列工具（应为二十项）、能力自描述、健康检查
.venv/bin/python hadoop/tools/mcp_client.py --url http://127.0.0.1:8765/mcp \
    --token-file <口令文件> list
.venv/bin/python hadoop/tools/mcp_client.py --url http://127.0.0.1:8765/mcp \
    --token-file <口令文件> describe

# 真实调用（参数写成 键=值，**选项要放在动作之前**）
.venv/bin/python hadoop/tools/mcp_client.py --url http://127.0.0.1:8765/mcp \
    --token-file <口令文件> call list_tasks
.venv/bin/python hadoop/tools/mcp_client.py --url http://127.0.0.1:8765/mcp \
    --token-file <口令文件> call get_task_status task_id=<任务编号>

# 只读资源：列出与读取
.venv/bin/python hadoop/tools/mcp_client.py --url http://127.0.0.1:8765/mcp \
    --token-file <口令文件> resources
.venv/bin/python hadoop/tools/mcp_client.py --url http://127.0.0.1:8765/mcp \
    --token-file <口令文件> read ml://scheme/cleaning_rules.v1.json
```

### 9.5 这一层最容易踩的三件事

| 现象 | 原因 | 处理 |
|---|---|---|
| 宿主机连不上 | 没加端口转发（虚拟机是 NAT 客户机） | 按 §9.3 加一条转发 |
| 请求全是 401 | 没带口令或口令不对 | 用 `token` 取口令；请求头写 `Authorization: Bearer …` |
| 服务启动后一会儿就没了 | 用普通后台方式启动，发起它的终端退出时被一起带走 | 用 `mcp_server.sh start`（它按独立会话启动）；已修正，若仍复现请贴 `logs` |
| 宿主机访问被拒（403） | 请求的 Host 不在允许列表里（端口转发配错或用 IP 直连） | 用 `127.0.0.1:<宿主机端口>` 访问；`status` 会打印当前允许的 Host |
| 服务报"明文传输"警告 | 这是启动时的刻意提醒 | 只在本机端口转发范围内使用；跨机使用需另加 TLS |
| 长时间挂着后变慢 | 访问日志与会话会累积；对端不复用会话时更明显 | 轮转 `var/mcp/server.log`；要求对端复用会话（一次连接多次调用） |

---

## 10. 停止与清理

```bash
hadoop/scripts/cluster.sh stop      # 停止全部守护进程
hadoop/scripts/mcp_server.sh stop   # 停止能力服务（如有在跑）
rm -rf var/ .hadoop-data/ .hadoop-logs/   # 清任务产物与 HDFS 数据（会丢数据）
```
