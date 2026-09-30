# -*- coding: utf-8 -*-
"""driver —— 任务编排与 CLI（阶段一/二拆分后）。

模块结构（docs/hadoop/driver-refactor-plan.md §3）：
  run_task.py   入口：main 只做 解析、分发、输出信封；信封/错误 + 执行链 Runner
  cli.py        参数解析：形态拆分（parse_args）+ 声明式规格校验（parse_argv）；
                CliError 定义于此，run_task 再导出（测试引用 rt.CliError 不受影响）
  commands.py   八个子命令各一个类 + 注册表（COMMAND_CLASSES / find_command）

stdout 只输出一个 JSON 信封；日志走 stderr；退出码遵循接口文档 §2。
本 __init__.py 使 driver 成为可导入的包，测试与编排代码得以复用其内部函数。
"""