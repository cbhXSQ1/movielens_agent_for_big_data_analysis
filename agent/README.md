# 第 5 节 Agent 侧（工具封装）

> 本节不实现清洗算法，也不实现评分公式。
> 清洗与评分全部由 Hadoop 侧 `hadoop/driver/run_task.py` 完成，
> Agent 只负责三件事：**组织任务 → 调工具 → 把结果翻译成人话**。

## 1. 三层结构

| 文件 | 作用 |
|---|---|
| `driver_client.py` | 调 driver CLI、解析 stdout 上那个 JSON 信封。只传话，不加工 |
| `tools.py` | 9 个 Agent 工具（8 个契约工具 + 1 个附加工具） |
| `explain.py` | 把 JSON 信封翻译成中文：五维对比 / 数据量变化 / 隔离统计 / 局限性。**页面所有数字的唯一产地** |
| `runtime/` | 阶段 3a：LangGraph 运行时 —— 对话与节点持久化、血缘、工具调用循环（`llm_loop.py` / `session.py` 已删除，由它取代） |
| `cli.py` | 命令行入口 `python3 -m agent.cli <工具>` |
| `http_api.py` | 零依赖 HTTP 服务：既有 11 个直取端点 + 阶段 6 契约端点（`/api/conversations…` 对话/SSE 推送流/发消息/上下文、`/api/tools` 工具表兜底、长任务进展与结构化结果）；`/api/chat` 自然语言入口走 runtime 的 LLM 主控循环 |

> 旧的纯规则意图解析（`intent.py`）与旧的一次性 LLM 增强（`agent.py` / `llm_parse.py`）
> 已按重构计划 T2.5 删除；自然语言入口只走 LLM 主控循环，模型不可用时结构化报错。

## 2. 9 个工具

| # | 工具 | 对应 driver 子命令 | 用途 |
|---|---|---|---|
| 1 | `validate_config(rules?, scoring?)` | `validate` | 校验清洗/评分方案 |
| 2 | `list_schemes()` | `schemes` | 列出已登记方案 |
| 3 | `start_cleaning_task(...)` | `start` | 发起清洗+评分任务（默认异步，立即返回 task_id） |
| 4 | `get_task_status(task_id)` | `status` | 查进度与阶段 |
| 5 | `get_task_result(task_id)` | `result` | 取权威结果（五维对比 + 数据量 + 隔离） |
| 6 | `get_samples(task_id, type, table, n)` | `samples` | 取清洗后/隔离区样例 |
| 7 | `get_report(task_id, format)` | `report` | 取完整评估报告 |
| 8 | `list_tasks()` | `tasks` | 列出最近 50 个任务 |
| 9 | `quick_clean_demo(...)` | **附加** `quick_clean` | 秒级演示性清洗，不经 Hadoop |

> 第 9 个是**附加工具，不属于 v1.0 契约子命令集**。
> 权威结果永远以 `start_cleaning_task` + `get_task_result` 为准。

## 3. 怎么用

```bash
# 在仓库根目录执行
python3 -m agent.cli validate
python3 -m agent.cli schemes --explain
python3 -m agent.cli start --exec local --foreground --tag demo
# 联调想更快：样本口径（评分表前 2000 行，数字不可用于汇报）
python3 -m agent.cli start --exec local --scope sample --foreground --tag selftest
python3 -m agent.cli status  --task-id <id> --explain
python3 -m agent.cli result  --task-id <id> --explain
python3 -m agent.cli samples --task-id <id> --type quarantine --table ratings --n 5 --explain
python3 -m agent.cli report  --task-id <id> --format md
python3 -m agent.cli tasks
python3 -m agent.cli quick-clean --sample 2000
```

默认输出 driver 的原始 JSON 信封（便于联调）；
加 `--explain` 额外输出一段中文解释。

作为库调用：

```python
from agent import tools, explain

env = tools.start_cleaning_task(exec_mode="local", foreground=True)
tid = env["task_id"]
res = tools.get_task_result(tid)
print(explain.explain_result(res))
```

## 4. 三条红线（课程评分点）

1. **不编造**：driver 失败或未完成时，原样返回错误信封，绝不用占位数字冒充结果。
2. **隔离 ≠ 修复**：`explain_result` 每次都同屏输出「数据量变化 + 隔离/去重/修复数 + 局限性」，
   并在结尾固定附一句口径提醒。
3. **权威结果以 start + result 为准**：`quick_clean_demo` 只用于现场演示与快速取数。

## 5. 已实测（2026-09-25，Windows 本地 + 真实 ml-1m）

用课程提供的注脏数据集跑 `--exec local --foreground`：

| 项 | 实测值 |
|---|---|
| 输入 | ratings 1,150,241 / users 6,946 / movies 4,465 |
| 输出 | ratings 1,000,209 / users 6,040 / movies 3,883 |
| 隔离 | 100,830 行 |
| 去重 | ratings 49,510 / movies 454 / users 726 |
| 修复 | R4_ms 13,503 / P1_text 48 / M2_strip 47 / U3_zip_plus4 73 |
| 综合分 | 95.07 → 99.95（+4.88） |

与队长给的黄金基线**逐项一致**。

> 唯一失败点是最后一阶段 `publish`（需要 `hdfs` 命令），
> 这是 Windows 上没有装 Hadoop 导致的，不是代码问题；
> 任务在 Ubuntu 虚拟机里能一路跑到 `done`。

## 6. 已发现的文档/代码小差异（待与队长确认）

| 项 | 接口文档 | 代码实测 |
|---|---|---|
| 阶段总数 | 序列列出 10 个 | `stage_total` 报 9 |
| `report` 默认格式 | 示例写 `--format md` | 代码默认 `json` |

两处都不影响联调（本 Agent 都显式传参），但汇报前建议统一口径。

## 7. 大模型层

> 重构方向：**LLM 主控为默认路径**；模型不可用时**直接结构化报错**（不回退规则）。
> 旧的「规则主路径 + 大模型可选兜底」与旧的措辞润色层已删除（计划 T2.5）。

`/api/chat`（自然语言入口）只走 runtime 的 LLM 主控循环：模型选工具并填参数 → 执行 →
结果回灌 → 基于真实结果组织回答。**模型不产出任何业务数字**——数字 100% 来自
工具返回结果（`number_guard` 数字溯源护栏会拦截无法溯源的数字）。

模型未配置 / 未启用时，`/api/chat` 返回结构化错误（`ok=false` + `error.code`），
不会静默降级成另一条路径。错误码：

| error.code | 含义 |
|---|---|
| `LLM_UNSUPPORTED` | 后端未启用大模型层 / 主控循环模块缺失 |
| `LLM_DISABLED` | 总开关未开（`AGENT_LLM_SUPPORTED` 未设）或请求 `mode=off` |
| `LLM_NOT_CONFIGURED` | 缺少 `api_base` / `api_key` / `model` |
| `LLM_LOOP_FAILED` | 循环中途失败（模型故障 / 超步 / 连续被策略拒绝） |

### ⚠️ 先开后端总开关（默认关）

```bash
export AGENT_LLM_SUPPORTED=1     # 总开关：决定 /health 里 llm.supported
```

**不设这个变量，整层就不生效**：`/health` 报 `llm.supported=false`，
即使请求里带了完整配置也不生效（后端会剔除请求里的 `supported`，防止前端绕过"默认关闭"）。

⚠️ **前端入口是常显的**（不是隐藏）：`supported=false` 时齿轮照常显示，
点开提示「后端未开启大模型增强：启动时设置 `AGENT_LLM_SUPPORTED=1`」。
这样页面只有一套布局，做好的面板也不会因为没配就"消失"。

### 怎么配（三选一，后者覆盖前者）

```bash
# ① 环境变量（推荐，不落盘）
export AGENT_LLM_SUPPORTED=1
export AGENT_LLM_ENABLED=1
export AGENT_LLM_API_BASE=https://…/v1     # 阿里云百炼：https://dashscope.aliyuncs.com/compatible-mode/v1
export AGENT_LLM_API_KEY=sk-…
export AGENT_LLM_MODEL=qwen-plus

# ② 本机 Ollama（机房没网时唯一能演示大模型能力的配置）
export AGENT_LLM_SUPPORTED=1
export AGENT_LLM_ENABLED=1
export AGENT_LLM_API_BASE=http://localhost:11434/v1
export AGENT_LLM_API_KEY=ollama            # 本地模型随便填个非空值
export AGENT_LLM_MODEL=qwen2.5:7b

# ③ 前端设置面板：单次请求带 body.llm，后端不落盘
```

`config/llm.settings.json` 也支持，但**已写进 `.gitignore`，禁止入库**（含 key）。

### 自检

```bash
curl -X POST http://localhost:8765/api/llm/test -H 'Content-Type: application/json' \
     -d '{"llm":{"enabled":true,"api_base":"…/v1","api_key":"…","model":"…"}}'
curl http://localhost:8765/health     # llm.supported / llm.configured
```

> 实现只用标准库 `urllib`，**不引 requests / openai sdk**——
> 保住「零第三方依赖、机房离线可跑」这个性质。

### 关于 `mode`

`mode` 是旧「规则主路径」时代的旋钮（`fallback` / `always` 的语义已随规则解析一起作废）。
现在只保留一个有效取值：`off` —— 请求里给 `mode=off` 时 `/api/chat` 直接结构化报错。

### 超时

默认 **8 秒**，硬上限 **30 秒**（`AGENT_LLM_TIMEOUT` 可配，配得再大也压到 30）。

30 秒是给**本地模型**留的余量——Ollama 跑 7B 模型生成 200 token 实测要 10~20 秒，
云端一般 1~3 秒就够（DeepSeek 实测 882ms）。

### 请求体编码（Windows 命令行用户注意）

服务端按 **UTF-8 → GBK → 坏字节替换** 三级容错解码，**收到坏编码不会崩**。

Git Bash 的 `curl` 在中文 Windows 上会按 **GBK** 发请求体（"今"的 GBK 是 `0xbd 0xf1`），
服务端会自动识别，所以命令行里发中文提问也能正常用。
浏览器一律发 UTF-8，不受影响。
