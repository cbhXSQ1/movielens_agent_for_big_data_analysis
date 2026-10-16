# -*- coding: utf-8 -*-
"""阶段 3a/3b（ag-runtime）：LangGraph 运行时地基 + 每对话一条推送流。

对外只暴露这几样：
    Runtime            程序化入口（建对话 / 跑一轮 / 读树 / 读血缘 / 开流）
    RuntimeState       图状态类型（含 add_messages 机制）
    build_graph        装配「模型 → 工具 → 策略 → 执行 → 回灌 → 循环」的图
    ConversationStore  对话与节点的权威持久化（SQLite）
    ArtifactStore      工具大结果外置存放（状态里只放引用）
    LocalToolBackend   本地工具执行后端（阶段 8 换 MCP 只换这里）
    ToolBackend        执行后端统一接口（阶段 8：本地直调 / MCP 调用共存）
    McpToolBackend     远程 MCP 执行后端占位（阶段 8：不连网，注入 McpClient 即用）
    RoutingBackend     按工具表「来源」字段路由 local / mcp（阶段 8）
    build_tool_backend 装配默认执行后端（运行时拿后端的唯一入口）
    StreamHub          每对话一条推送流的中枢（传输层无关，T3.4）
    StreamSubscription 单条流的消费句柄（同步 / 异步两用，T3.4）
    tool_table         接口文档 §3.7 形态的工具表（T3.6）

覆盖任务：
    T3.1 对话模型与持久化（对话、消息、上下文）
    T3.2 对话节点与血缘模型（父子关系，为树打底）
    T3.3 工具调用循环落到 LangGraph 图
    T3.4 每对话一条推送流（事件类型按接口文档 §3.6）
    T3.5 异步调度：流式输出与后台事件互不覆盖（run_turn_async → AsyncSqliteSaver）
    T3.6 工具表主动推送（连接时 + 变化时）

覆盖任务（阶段 4，ag-longtask）：
    T4.1 长任务登记与挂起（执行出口识别长耗时工具，句柄写入对话上下文，不阻塞）
    T4.2 完成检测与中断注入（终态 → task_resumed/result_payload → 模型续写）
    T4.3 重启后按执行侧核对（probe 可注入，绝不本地自证）

覆盖任务（阶段 5，ag-offline）：
    T5.1 离线直调通道：工具名 + 可选参数 → 补默认 / 校验 / **不经模型**直调
    T5.2 与 LLM 模式共用同一张工具表与同一个执行后端（含长任务登记与推送）

限制（留待后续阶段）：
    * 真实 Hadoop/MCP 执行侧未接入 —— 本层用 `FakeTaskExecutor` 模拟完成与核对，
      接口（`probe(record)` / 登记句柄）已固定，阶段 8 换真实后端不改上层；
    * 分支/跳回属阶段 7（本层已记父节点，届时无需改表）。
"""

from .artifacts import ArtifactStore
from .graph import (ARTIFACT_THRESHOLD, MAX_FINAL_RETRIES, MAX_LOOP_SECONDS,
                    MAX_PLAN_RETRIES, MAX_STEPS, RuntimeState, build_graph,
                    build_tool_schemas, envelope_of)
from .long_task import (ABORTED, FAILED, LongTaskManager, QUEUED, RUNNING,
                        STATUS_CN, SUCCEEDED, TERMINAL, FakeTaskExecutor,
                        describe_task, estimated_seconds_for, interrupt_text,
                        is_long_running, result_payload, result_summary,
                        task_view)
from .offline import (POLICY_REJECTED, TOOL_EXPLAIN_KIND, UNKNOWN_TOOL, USAGE,
                      explain_envelope, run_offline, tool_defaults,
                      validate_offline_call)
from .runtime import (DEFAULT_ARTIFACT_DIR, DEFAULT_DB_PATH, Runtime,
                      default_facts_factory, default_provider_factory)
from .store import ConversationStore, open_connection, summarize
from .stream import (ERROR, EVENT_TYPES, LONG_RUNNING_TOOLS, MESSAGE_DELTA,
                     MESSAGE_DONE, NODE_ADDED, RESULT_PAYLOAD, StreamHub,
                     StreamSubscription, TASK_RESUMED, TASK_STATUS,
                     TOOLS_SNAPSHOT, make_event, tool_table,
                     tool_table_fingerprint)
from .tool_exec import (LocalToolBackend, LongTaskBackend, McpToolBackend,
                        RoutingBackend, ToolBackend, build_tool_backend)

__all__ = [
    "Runtime", "RuntimeState", "build_graph", "build_tool_schemas",
    "envelope_of", "ConversationStore", "ArtifactStore", "LocalToolBackend",
    "LongTaskBackend", "ToolBackend", "McpToolBackend", "RoutingBackend",
    "build_tool_backend",
    "open_connection", "summarize", "default_provider_factory",
    "default_facts_factory", "DEFAULT_DB_PATH", "DEFAULT_ARTIFACT_DIR",
    "MAX_STEPS", "MAX_LOOP_SECONDS", "MAX_PLAN_RETRIES", "MAX_FINAL_RETRIES",
    "ARTIFACT_THRESHOLD",
    "StreamHub", "StreamSubscription", "make_event", "tool_table",
    "tool_table_fingerprint", "LONG_RUNNING_TOOLS",
    "TOOLS_SNAPSHOT", "MESSAGE_DELTA", "MESSAGE_DONE", "TASK_STATUS",
    "TASK_RESUMED", "RESULT_PAYLOAD", "NODE_ADDED", "ERROR", "EVENT_TYPES",
    # 阶段 4：长任务
    "LongTaskManager", "FakeTaskExecutor", "task_view", "describe_task",
    "is_long_running", "estimated_seconds_for", "result_summary",
    "result_payload", "interrupt_text", "STATUS_CN", "TERMINAL",
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "ABORTED",
    # 阶段 5：离线直调
    "run_offline", "validate_offline_call", "explain_envelope",
    "tool_defaults", "TOOL_EXPLAIN_KIND",
    "UNKNOWN_TOOL", "USAGE", "POLICY_REJECTED",
]
