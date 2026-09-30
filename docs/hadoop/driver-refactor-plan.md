# driver 重构方向与命名约定（下一步改动计划）

> 状态：方向已定稿；**阶段一、二已完成**（2026-09-30 上，纯结构搬家 + 解析规格化，
> 本地 329 测试全绿，独立审查通过并修复 #1）；**阶段三、四已完成**（2026-09-30 下，
> pipeline 阶段类 + hdfsio 提交合并 + 清理，本地 343 测试全绿）；
> **剩余 VM 侧验证项**：阶段三小样本集群冒烟（`--scope sample`）、阶段四末尾全量对账一次；
> 阶段五（v1.1）与 Agent v2 阶段 1-4 同步。
> 范围：hadoop 侧 driver。分两部分——**结构重构**（第 1-6 节，对外契约不动，v1.0 保持原样）与**精细任务加性升级**（第 7 节，v1.1，先改接口文档再实现）。
> 配套文档：`docs/agent/llm-agent-plan.md`（Agent v2 计划，两边的工具与策略必须同源）。
> 总原则：行为不变、数字不变、测试全绿、小步提交、任何一步出问题可单独回退。
> 结构原则：driver 现结构已极为冗杂（见第 1 节），一律**跨步重构**——该拆的拆、该换的换，禁止在旧结构上打补丁续命；补丁只允许用于独立缺陷修复。

## 1. 为什么要改（都是现状里真实存在的问题）

1. 阶段顺序定义了两遍：`STAGES` 常量（run_task.py L52）和 `run_cluster()` 里的实际顺序（L739-796）。两处靠人肉保持一致，改一处忘另一处，进度条就会说谎。
2. 模式判断埋在 if/else 里：`_execute` 判断 local/cluster（L1185-1188）；本地模式跑完后用循环"补写"九个阶段进度（L856-857），进度是表演出来的。
3. `Runner` 一个类约 570 行（L595-1167）：阶段标记、作业提交、HDFS 收发、评分、隔离分拣、报告渲染、发布全搅在一起，找边界靠翻行号。
4. 两条作业提交路径：`submit_stage.sh`（单输入）与 `_raw_job`（多输入裸 jar）。参数已经漂移——脚本关了推测执行、设了两个输出分隔符属性；`_raw_job` 没关推测执行、分隔符只设了一个（L682-683 对比 submit_stage.sh L87-90、L96-97）。
5. 环境变量前缀写了三遍（L694-695、L716-717、L1124-1125），且固定指仓库内 `.vendor`，与集群侧用的 `/opt/hadoop` 双来源并存，换机器必绊人。
6. 死码与含糊命名：`meta` 死参数（L865 读出、L879 传入、L976 形参，函数体内从未使用；且集群模式根本没有 metadata.json 的写入点）；`sc` 单次变量（L741）；`R`（45 处）、`self.d`（35 处）这类单字母名贯穿一百多行。

## 2. 命名约定（硬性，今后 hadoop 侧代码都按这个来）

1. 方法名一律"动词 + 对象"：
   `build_stages`、`execute_all_stages`、`execute_stage`、`execute_command`、`submit_job`、`download_directory`、`save_status`、`load_status`、`get_task_directory`、`find_running_task`、`build_result_dict`、`build_markdown_text`、`select_pipeline`。
2. 禁止单独出现 run、plan、do、handle、process 这类没有对象的动词。
3. 名词只留给数据类和属性：`ParsedCommand`、`JobSpec`、`TaskContext`。
4. 风格：不用 dataclass、不用类型注解、不用 ABC 和装饰器；普通类、`%` 格式化、显式循环；保持 Python 3.8 兼容；沿用现有中文注释习惯。

## 3. 目标结构

```
hadoop/driver/
  run_task.py    入口：main 只做 解析、分发、输出信封
  cli.py         参数解析：把 argv 拆成命令名、选项、操作数三组
  commands.py    各命令一个类 + 命令注册表（含 v1.1 新命令）
  pipeline.py    管道基类、集群/本地子类、各阶段类
  hdfsio.py      唯一作业提交入口、HDFS 收发、计数器解析
  task.py        任务状态：目录、状态机、进度、单任务锁
  report.py      报告构建：result 字典与 markdown 文本
```

## 4. 接口草案（朴素写法）

```python
# pipeline.py
class Pipeline:
    def build_stages(self):
        # 返回本管道的阶段清单
        raise NotImplementedError

    def execute_all_stages(self):
        # 按清单顺序逐个执行
        for stage in self.build_stages():
            stage.execute_stage(self.ctx)

class ClusterPipeline(Pipeline):
    def build_stages(self):
        return [CleanUsersStage(), CleanMoviesStage(), CleanRatingsStage(),
                StatsStage(), ScoreStage("before"), ScoreStage("after"),
                FinalizeStage(), PublishStage()]

class LocalPipeline(Pipeline):
    def build_stages(self):
        return [LocalEngineStage()]

class Stage:
    def execute_stage(self, ctx):
        raise NotImplementedError

class CleanUsersStage(Stage):
    pass

class ScoreStage(Stage):
    def __init__(self, side):
        self.side = side          # "before" 或 "after"

def select_pipeline(ctx):
    # 按运行模式选择实现
    if ctx.mode == "local":
        return LocalPipeline(ctx)
    return ClusterPipeline(ctx)
```

```python
# commands.py
class Command:
    name = ""
    def __init__(self, ctx, args):
        self.ctx = ctx
        self.args = args
    def execute_command(self):
        raise NotImplementedError

class StartCommand(Command):
    name = "start"
    pass

COMMAND_CLASSES = [StartCommand, StatusCommand, ResultCommand, SamplesCommand,
                   ReportCommand, TasksCommand, ValidateCommand, SchemesCommand,
                   ScoreCommand]

def find_command(name):
    for command_class in COMMAND_CLASSES:
        if command_class.name == name:
            return command_class
    return None
```

```python
# cli.py
class ParsedCommand:
    def __init__(self, name, options, operands):
        self.name = name
        self.options = options
        self.operands = operands

def parse_argv(argv, specs):
    # specs：每个子命令的声明式参数表（名字、是否必填、默认值、取值范围）
    # 校验失败抛 CliError("USAGE")
    return ParsedCommand("", {}, [])
```

```python
# hdfsio.py
class JobSpec:
    def __init__(self, script, inputs, output, reduces=0, mapper_args="",
                 reducer_args=None, extra_files="", key_fields=None, job_name=None):
        pass

class StreamingSubmitter:
    def submit_job(self, job_spec):
        # 唯一提交路径；submit_stage.sh 与 _raw_job 合并到这一处
        pass

class HadoopShell:
    def execute_command(self, command, log_path):
        # 环境变量前缀只在这里出现一次
        pass
    def download_directory(self, hdfs_dir, local_path):
        pass
    def path_exists(self, hdfs_path):
        pass
```

```python
# task.py
class TaskStatus:
    def save_status(self, **fields):
        # 写入并顺带计算阶段号与百分比
        pass
    def load_status(self):
        pass

class TaskStore:
    def get_task_directory(self, task_id):
        pass
    def find_running_task(self):
        pass
```

```python
# report.py
class ReportBuilder:
    def build_result_dict(self, counts, before, after, schemes):
        pass
    def build_markdown_text(self, counts, before, after, schemes):
        pass
```

## 5. 现有代码对应关系

| 现在 | 变成 |
|---|---|
| `parse_args` 加各 `cmd_*` 里的取值校验 | `cli.py` 的解析器加声明式规格 |
| `COMMANDS` 函数表 | `commands.py` 的命令类注册表 |
| `_execute` 里的 if/else 模式判断 | `select_pipeline` 一处判断 |
| `Runner.run_cluster` 一大段 | `ClusterPipeline.build_stages()` 给出清单，逻辑进各阶段类 |
| `Runner.job` 加 `_raw_job` 两条路 | 一个 `StreamingSubmitter` |
| `finish` 加 `_write_report` 加 `publish` | 管道收尾委托 `report.py` 与 `PublishStage` |
| 三处重复的环境变量前缀 | `HadoopShell` 里一处 |
| `write_status`、`running_task` 等散函数 | `TaskStatus`、`TaskStore` 两个类 |
| `R`、`self.d`、`sc`、`meta` 这类名字 | 随重构去掉，按命名约定重起 |

## 6. 分阶段实施与验收（结构重构部分）

- 阶段一：拆文件加命令类，纯搬家不改逻辑。验收：本地测试全绿（不碰集群）。
- 阶段二：解析规格化，默认值、类型、必填校验集中。验收：本地测试 + 错误信封抽样比对（USAGE、TASK_NOT_FOUND、CONFIG_INVALID 各一例）。
- 阶段三：管道与阶段类落地，工厂接管模式判断（此步动集群路径）。验收：本地黄金测试全绿 + 小样本集群冒烟（`--scope sample`）。
- 阶段四：清理。合并提交路径、统一环境前缀、删死参数与单次变量、重命名收尾。验收：本地全绿 + **全量对账一次**——这是结构重构的收尾验证，也是重构期的唯一一次全量。
- 阶段五：精细任务（第 7 节）。验收：本地全绿；v1.1 新命令用小样本验收；**全量只再跑一次**（full 回归）。
- **测试口径（省时）**：除上面标出的两个全量检查点（阶段四末尾、阶段五末尾）外，一律本地测试或小样本；不得逐阶段重复跑全量。

每一步独立提交，测试全绿才进下一步。阶段一至四不得改变：黄金数字、信封格式、退出码、产物路径。

## 7. 精细任务与接口升级（v1.1，配套 Agent v2）

目标：在"全流程"之外支持两类独立任务，让 Agent 能按需组合（只清洗、只评分、或组合执行）。**加性升级**：全流程行为不变、旧命令字段不变；先改 `docs/hadoop/agent-interface.md` 并把 `interface_version` 升到 1.1，再实现。

### 7.1 新命令与参数

1. `start --task-type clean [--tables users,movies,ratings]`
   - 只跑清洗链（上传 → 三表清洗 → 统计 → 产物取回与隔离分拣），不评分、不发布。
   - 产物：cleaned、quarantine、counts（quarantine / dedupe / fix）、stats；result 里 scores 为空且明确标注"未评分"。
2. `score --source raw|published|task --from-task <id> [--scoring <path>] [--foreground] [--tag]`
   - `raw`：对原始三表评分（等价于清洗前评分）。
   - `published`：对发布区 cleaned 三表评分（要求发布区存在）。
   - `task`：对指定任务的 cleaned 产物评分（要求该任务 succeeded 且产物存在）。
   - 产物：metrics/、counts；任务信封与 status 沿用现有形状。
3. 阶段级命令（`run-stage --task-id --stage`）暂不暴露：先完成阶段类拆分，确有需要再开；工具化的粒度以任务类型为主。

### 7.2 依赖与互斥（driver 与 agent 策略层共用同一份规则）

| 操作 | 前置依赖 | 互斥/并行 |
|---|---|---|
| start（full） | 原始数据可读 | 占用任务锁（串行） |
| start（clean） | 原始数据可读 | 占用任务锁 |
| score（raw） | 原始数据可读 | 占用任务锁 |
| score（task） | 目标任务 cleaned 存在 | 占用任务锁 |
| score（published） | 发布区存在 | 占用任务锁 |
| publish | finalize 产物存在 | 发布互斥 + 版本哈希保护（D-017） |
| 读命令（status/result/samples/report/tasks/schemes） | 无 | 只读，可并行 |

- 依赖不满足时返回新的明确错误码（建议 `DEPENDENCY_MISSING`，退出码 2），信息里写清缺什么：哪个任务、哪份产物。
- full 任务内部仍按原顺序；clean 只跑前段；score 只跑评分段。

### 7.3 验收（省时口径）

- full 行为与黄金数字不变：**全量对账只做一次**，放在阶段五末尾；重构期的全量已由阶段四覆盖，不重复。
- clean-only：**样本级验收**——与同一样本的 full cleaned 逐行一致；隔离 / 去重 / 修复计数一致；无 metrics 产物。全量版可选（视时间，与阶段四的 cleaned 对比）。
- score（raw / task / published）：**样本级验收**——五维结果与对应样本的 full 侧一致。
- 依赖错误码单例测试：缺任务、缺产物、发布区不存在各一例。
- 所有全量运行共用一份原始数据、一个检查点记录，不重复跑；每跑一次全量都在记录里说明用途。

## 8. 与 agent 侧的关系

- 结构重构（阶段一至四）对 agent 完全透明：八个子命令、JSON 信封、退出码不变，`agent/driver_client.py` 无需改动。
- 精细任务（v1.1）是加性升级：agent 工具表新增 `start_clean_task` 与 `score_task` 两项（见 `docs/agent/llm-agent-plan.md` 第 3 节），旧工具不变。
- 依赖与互斥规则两边同源：driver 自身必须校验（不能只靠 agent 策略层），agent 策略层负责在调用前拦截与解释。
- 对外契约若确需变更，必须先改 `docs/hadoop/agent-interface.md` 并升 `interface_version`，不允许悄悄改字段。

## 9. 执行前待拍板

1. 文件粒度：七个模块，还是合并成四个（cli 与 commands 合、hdfsio 与 task 合）。
2. 参数解析：自研解析加声明式规格，还是换标准库 argparse。
3. 阶段粒度：表级加副作用阶段共约八个类（推荐），还是每趟作业一个类。
4. 是否引入 TaskContext 作为各层共享上下文（携带任务号、三套路径、配置对象、模式、scope）；推荐引入。
5. 验收分工：Windows 侧做阶段一、二与本地测试；VM 侧做小样本冒烟与两个全量检查点（阶段四末尾、阶段五末尾）。
6. 启动时间：阶段一、二立即做；阶段三、四紧随其后；阶段五与 Agent v2 阶段 1-4 同步。
7. 新错误码命名与退出码：建议 `DEPENDENCY_MISSING`，退出码 2。
8. clean 任务是否默认也取回隔离区与计数（建议是）。
9. score 是否需要 before/after 的显式参数，还是由 source 隐式决定（建议隐式）。

## 9.1 拍板记录（2026-09-30，审阅后定稿）

> 结论先行：**先做阶段一、二（纯结构搬家，对外契约不动），与 Agent 阶段 0 并行**；
> 阶段一、二产出的模块既是目标结构的骨架，也为阶段三、四铺路。

| # | 决策 | 理由 |
|---|---|---|
| 1 | 文件粒度 | **七个模块**（按 §3 目标结构）。阶段一、二只先落地 `cli.py` + `commands.py`；`pipeline / hdfsio / task / report` 随阶段三、四拆 Runner 时落地。理由：每模块单一职责；cli（argv 形态）与 commands（命令语义）生命周期不同；hdfsio（HDFS 收发）与 task（状态机）是两个关注点，合并会退化成杂物袋 |
| 2 | 参数解析 | **自研声明式规格**（计划 §4 cli.py 草图）。理由：① 工程红线 Python 3.8 兼容，argparse 的 `exit_on_error` 是 3.9+；② 契约要求错误以 **stdout JSON 信封**输出、退出码 0/2/3/4/5/6，argparse 默认 stderr 文本 + SystemExit 2 需要大量适配；③ 零依赖红线 |
| 3 | 阶段粒度 | **表级加副作用阶段共约八个类**（推荐项）——`CleanUsersStage / CleanMoviesStage / CleanRatingsStage / StatsStage / ScoreStage(side) / FinalizeStage / PublishStage` + 本地引擎类。理由：阶段=作业链的语义单元，每趟作业一类会得到 20+ 个琐碎类 |
| 4 | TaskContext | **引入**。携带 task_id、三套路径（任务目录/HDFS/raw）、schemes、mode、scope；Runner 的构造参数随阶段三迁移进 ctx |
| 5 | 验收分工 | **阶段一、二 + 本地测试**在本机（Ubuntu，等效 Windows 侧职责）完成；VM 侧负责阶段三起的小样本冒烟与两个全量检查点（阶段四末尾、阶段五末尾）。全量对账只跑两次，不逐阶段重复 |
| 6 | 启动时间 | 阶段一、二**立即执行**（本记录随本次提交落地）；阶段三、四紧随其后（下一轮）；阶段五与 Agent v2 阶段 1-4 同步 |
| 7 | 新错误码 | **`DEPENDENCY_MISSING`，退出码 2**（v1.1 实现时落进 ERROR_CODES，并先改接口文档升 interface_version） |
| 8 | clean 取回 | **默认取回隔离区与计数**——clean 任务的产物与 full 的前段一致（cleaned / quarantine / counts），便于后续 score(task) 复用 |
| 9 | score 参数 | **由 source 隐式决定**——source=raw → before 侧，published/task → after 侧；不引入显式 before/after 参数，减少自相矛盾的组合面 |
