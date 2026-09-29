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
| `session.py` | 运行口径快照：追问沿用第一轮选定的 `cluster/local` 与 `full/sample` |
| `agent.py` | 自然语言入口 `respond()`：一句话 → 工具调用 → 中文回答 |
| `cli.py` | 命令行入口 `python3 -m agent.cli <工具>` |
| `http_api.py` | 零依赖 HTTP 服务（11 个端点，给第 6 节前端） |

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

## 7. 大模型可选层（**默认关闭**）

> 官方未要求接入大模型（实验资料第 95 行：实现方式由学生自主设计）。
> 这一层是**可选增强**，不配置时行为与纯规则 100% 一致。

它只做两件事：**① 帮用户把话理解成调用；② 把自述句说得更顺**。
**不生成清洗规则、不产出任何数字**——数字 100% 来自 Hadoop 真实运行。

### ⚠️ 先开后端总开关（默认关）

```bash
export AGENT_LLM_SUPPORTED=1     # 总开关：决定 /health 里 llm.supported，前端据此显示入口
```

**不设这个变量，整层就当作不存在**：`/health` 报 `llm.supported=false`，
前端把设置入口整块隐藏；即使请求里带了完整配置也不生效
（后端会剔除请求里的 `supported`，防止前端绕过"默认关闭"）。

| 后端启动时 | `/health.l‌lm.supported` | 页面 |
|---|---|---|
| 默认（不设） | `false` | **完全看不到大模型入口** |
| `AGENT_LLM_SUPPORTED=1` | `true` | 入口出现，可配置 |

### 怎么配（三选一，后者覆盖前者）

```bash
# ① 环境变量（推荐，不落盘）
export AGENT_LLM_SUPPORTED=1
export AGENT_LLM_ENABLED=1
export AGENT_LLM_API_BASE=https://…/v1     # 阿里云百炼：https://dashscope.aliyuncs.com/compatible-mode/v1
export AGENT_LLM_API_KEY=sk-…
export AGENT_LLM_MODEL=qwen-plus
# mode：fallback(默认，**只在规则彻底听不懂时兜底**) / always(每句都问，**演示用这个**)
export AGENT_LLM_MODE=fallback

# ② 本机 Ollama（机房没网时唯一能演示大模型能力的配置）
export AGENT_LLM_SUPPORTED=1
export AGENT_LLM_ENABLED=1
export AGENT_LLM_API_BASE=http://localhost:11434/v1
export AGENT_LLM_API_KEY=ollama            # 本地模型随便填个非空值
export AGENT_LLM_MODEL=qwen2.5:7b
export AGENT_LLM_MODE=always               # 演示用 always，否则看不出效果

# ③ 前端设置面板：单次请求带 body.llm，后端不落盘
```

`config/llm.settings.json` 也支持，但**已写进 `.gitignore`，禁止入库**（含 key）。

### 自检

```bash
curl -X POST http://localhost:8765/api/llm/test -H 'Content-Type: application/json' \
     -d '{"llm":{"enabled":true,"api_base":"…/v1","api_key":"…","model":"…"}}'
curl http://localhost:8765/health     # llm.supported / llm.configured
```

### 三层护栏（防编造）

1. **结构性隔离**：`explain` 产出的数字文本**物理上不传给大模型**；
   只有 4 类"无事实句"允许润色。
2. **占位符模板**：润色前把数字换成 `{v0}{v1}…`，它眼里没数字，回来回填原值。
3. **输出后校验**：占位符对不上 / 出现新数字 / 命中「修复了、已解决」等禁用措辞 /
   长度超标 → **丢弃润色，返回原句**。

不可用时（没配、没网、超时、返回不合法）一律**回落规则解析**，
并在返回体 `llm.note` 里写明真实原因，**功能不降级**。

> 实现只用标准库 `urllib`，**不引 requests / openai sdk**——
> 保住「零第三方依赖、机房离线可跑」这个性质。

### `mode` 决定它到底会不会被用到

| mode | 行为 | 什么时候用 |
|---|---|---|
| `fallback`（默认） | **只有规则彻底听不懂（识别为 `unknown`）时才兜底问它** | 日常。规则仍是主路径 |
| `always` | 每句都问一次 | **汇报现场演示**，否则看不出效果 |
| 其它值 | 当全关 | — |

> ⚠️ **为什么不做"置信度阈值"**：规则一旦命中关键词置信度就 ≥0.8
> （`intent.py`：`0.5 + 0.15 × 命中词数`，常用说法实测 0.80~0.85）。
> 所以"低于某阈值才去问"是死条件——试过 0.5（永不触发）、0.8（0.80 不小于 0.8，还是不触发）。
> 与其留一个调了也没用的旋钮，不如把 `fallback` 语义写死，想演示就用 `always`。
> （2026-09-29 由第 6 节前端同学核出来。）
