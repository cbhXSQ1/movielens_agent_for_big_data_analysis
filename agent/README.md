# 第 5 节 Agent 侧（工具封装）

> 本节不实现清洗算法，也不实现评分公式。
> 清洗与评分全部由 Hadoop 侧 `hadoop/driver/run_task.py` 完成，
> Agent 只负责三件事：**组织任务 → 调工具 → 把结果翻译成人话**。

## 1. 三层结构

| 文件 | 作用 |
|---|---|
| `driver_client.py` | 调 driver CLI、解析 stdout 上那个 JSON 信封。只传话，不加工 |
| `tools.py` | 9 个 Agent 工具（8 个契约工具 + 1 个附加工具） |
| `explain.py` | 把 JSON 信封翻译成中文：五维对比 / 数据量变化 / 隔离统计 / 局限性 |
| `cli.py` | 命令行入口 `python3 -m agent.cli <工具>` |

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
