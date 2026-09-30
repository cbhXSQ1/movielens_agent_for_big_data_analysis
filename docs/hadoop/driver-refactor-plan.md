# driver 重构方向与命名约定（下一步改动计划）

> 状态：方向已与组长讨论定稿；**汇报完成后执行**。
> 范围：hadoop 侧 driver 内部结构重构。**对外契约不动**——`docs/hadoop/agent-interface.md` v1.0 的八个子命令、JSON 信封、退出码一律保持原样；agent 侧改动与本次同步进行（由组长统一安排）。
> 总原则：行为不变、数字不变、测试全绿、小步提交、任何一步出问题可单独回退。

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
  commands.py    八个命令各一个类 + 命令注册表
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
                   ReportCommand, TasksCommand, ValidateCommand, SchemesCommand]

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

## 6. 分阶段实施与验收

- 阶段一：拆文件加命令类，纯搬家不改逻辑。验收：Windows 本地模式测试全绿。
- 阶段二：解析规格化，默认值、类型、必填校验集中。验收：错误信封抽样比对（USAGE、TASK_NOT_FOUND、CONFIG_INVALID 各一例）。
- 阶段三：管道与阶段类落地，工厂接管模式判断。**此步动集群路径**，验收：本地黄金测试全绿，VM 全量复跑逐字节对账。
- 阶段四：清理。合并提交路径、统一环境前缀、删死参数与单次变量、重命名收尾。验收同阶段三，VM 再复跑一次。
- 阶段五（迭代二）：阶段类加工具名与策略字段，接工具策略层（保护性类：依赖、互斥、前置条件、参数白名单）。

每一步独立提交，测试全绿才进下一步。所有阶段不得改变：黄金数字、信封格式、退出码、产物路径。

## 7. 与 agent 侧的关系

- 本次重构对 agent 完全透明：八个子命令、JSON 信封、退出码不变，`agent/driver_client.py` 无需改动。
- agent 侧同期改动由组长统一安排；driver 的 Stage 类是将来"阶段工具化"的天然单元（一个阶段对应一个工具）。
- 对外契约若确需变更，必须先改 `docs/hadoop/agent-interface.md` 并升 `interface_version`，不允许悄悄改字段。

## 8. 执行前待拍板

1. 文件粒度：七个模块，还是合并成四个（cli 与 commands 合、hdfsio 与 task 合）。
2. 参数解析：自研解析加声明式规格，还是换标准库 argparse。
3. 阶段粒度：表级加副作用阶段共约八个类（推荐），还是每趟作业一个类。
4. 是否引入 TaskContext 作为各层共享上下文（携带任务号、三套路径、配置对象、模式、scope）；推荐引入。
5. 验收分工：Windows 侧做阶段一、二与本地测试；VM 侧做阶段三、四的集群对账。
6. 启动时间：阶段一、二在汇报后立即做；阶段三、四赶在迭代二开发前；阶段五归迭代二。
