# -*- coding: utf-8 -*-
"""mcpserver · 对外契约层（纯常量与纯函数，不连网、不依赖官方协议包）。

本包把「对宿主机 Agent 开放的能力」写成一份可被程序读、也可被人读的清单：

  * `contract` —— 契约版本、状态取值、错误码与退出码映射、信封构造；
  * `tools`    —— 二十项能力的名称、中文说明、参数与默认值、读写属性、耗时标注。

分层约定（重要）：本包**不**导入官方协议实现包，也不导入 driver 的执行链，
原因有二 —— 一是单元测试要能在只有系统自带解释器的环境里跑起来；二是把
「描述」与「执行」分开，工具表可以不启动任何依赖就列出来。

命名说明：包目录叫 `mcpserver` 而不是 `mcp`，因为官方实现包的名字就是 `mcp`；
若同名，`PYTHONPATH` 里先出现的目录会把官方包盖掉（测试与常驻服务都会受影响）。
"""

from .contract import (          # noqa: F401
    INTERFACE_VERSION,
    DRIVER_INTERFACE_VERSION,
    STATUSES,
    TERMINAL_STATUSES,
    StructuredError,
    EXIT_CODE_MAP,
    TRANSPORT_CODE_MAP,
    STRUCTURED_ERROR_CODES,
    DRIVER_NATIVE_CODES,
    code_for_exit,
    code_for_transport,
    normalize_error,
    envelope_ok,
    envelope_error,
)
from .tools import (             # noqa: F401
    TOOLS,
    TOOL_INDEX,
    tool_names,
    describe_capabilities,
    get_tool,
)
