# -*- coding: utf-8 -*-
"""阶段 3a/3b（ag-runtime）· 程序化入口：给定对话编号与用户消息，跑一轮并推送事件。

    from agent.runtime import Runtime
    rt = Runtime()
    cid = rt.create_conversation(title="第一次对话")
    sub = rt.open_stream(cid)                  # 阶段 3b：为这段对话开一条推送流
    result = rt.run_turn(cid, "帮我看看之前跑过哪些任务")
    for event in sub.drain():                  # tools_snapshot / node_added /
        print(event)                           # message_delta… / message_done
    print(result["final"])

一轮 = 一个用户节点 + 一个助手节点；消息历史（LLM 上下文）随检查点累积，
节点与血缘落在自建权威表。进程重启后两处都还在（同一个 SQLite 文件）。

阶段 3a：一次性返回（`run_turn`）。
阶段 3b（本次）：
  * T3.4 每对话一条推送流（`open_stream` + 事件，传输层无关）；
  * T3.5 异步调度（`run_turn_async` 用 AsyncSqliteSaver，流式与后台事件互不覆盖）；
  * T3.6 工具表主动推送（订阅时快照 + `notify_tools_changed()` 再推）。
数据模型与既有一次性行为保持不变。
"""

import os
import time

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from .. import explain, llm, protocol, tool_facts, tools
from ..llm import ModelError, NOT_CONFIGURED
from . import artifacts_ref
from .artifacts import ArtifactStore
from .graph import MAX_LOOP_SECONDS, NullProvider, build_graph, envelope_of
from .long_task import LongTaskManager, completion_message, task_view
from .offline import run_offline
from .store import ConversationStore, open_connection, summarize
from .stream import (ARTIFACT_ADDED, ERROR, MESSAGE_DELTA, MESSAGE_DONE,
                     NODE_ADDED, StreamHub)
from .tool_exec import LocalToolBackend, LongTaskBackend, build_tool_backend

_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_HERE)

#: 运行期产物目录（`agent/var/` 已被 .gitignore 忽略）
DEFAULT_VAR_DIR = os.path.join(_AGENT_DIR, "var")
DEFAULT_DB_PATH = os.path.join(DEFAULT_VAR_DIR, "agent_runtime.sqlite")
DEFAULT_ARTIFACT_DIR = os.path.join(DEFAULT_VAR_DIR, "artifacts")


def default_provider_factory():
    """按当前配置装配模型接入层；不可用就抛结构化错误（不静默回退）。"""
    config = llm.load()
    if not config.usable:
        raise ModelError(NOT_CONFIGURED,
                         "大模型未配置或配置不完整（需要 api_base / api_key / model，"
                         "且总开关与 enabled 打开）")
    return llm.build_provider(config)


def default_facts_factory():
    """策略校验事实：任务锁来自真实任务列表，原始数据可读来自真实探测。"""
    try:
        return tool_facts.collect(tasks=tools.list_tasks())
    except Exception:
        return tool_facts.collect()


def _node_payload(node):
    """node_added 事件的字段（接口文档 §3.6：编号、父节点、角色、时间）。"""
    return {"node_id": node["id"], "parent_id": node["parent_id"],
            "role": node["role"], "created_at": node["created_at"]}


class Runtime(object):
    """运行时：对话存储 + 检查点 + 工具调用循环图 + 每对话推送流的持有者。"""

    def __init__(self, db_path=None, artifact_dir=None, provider_factory=None,
                 tool_backend=None, facts_factory=None, stream_hub=None,
                 task_probe=None, task_poll_interval=None, mcp_client=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.artifact_dir = artifact_dir or DEFAULT_ARTIFACT_DIR
        self.store = ConversationStore(self.db_path)
        self.artifacts = ArtifactStore(self.artifact_dir)
        self.streams = stream_hub or StreamHub()
        self._provider_factory = provider_factory or default_provider_factory
        # 阶段 8 T8.1：默认执行后端经抽象工厂装配（本地直调 + MCP，按来源路由）。
        # T8.9：注入的 `mcp_client` 交给工厂 —— source=mcp 的工具就走这个客户端。
        self._tool_backend = tool_backend or build_tool_backend(mcp_client=mcp_client)
        self._facts_factory = facts_factory or default_facts_factory
        # 阶段 4：长任务登记 / 唤醒续写 / 重启核对的中枢（挂对话上下文的长期状态）。
        # 后台进度轮询探针可注入：真实运行传远端探针（`RemoteTaskProbe`），
        # 测试传假探针；不传则不自动轮询（既有行为不变）。
        poll_kwargs = {}
        if task_poll_interval is not None:
            poll_kwargs["poll_interval"] = task_poll_interval
        self.long_tasks = LongTaskManager(self.store, self.streams,
                                          probe=task_probe, **poll_kwargs)
        self.long_tasks.bind(self)
        self._checkpoint_conn = open_connection(self.db_path)
        self._checkpointer = SqliteSaver(self._checkpoint_conn)
        # 只读用的图（从不调模型），供读取检查点里的消息历史
        self._reader = build_graph(NullProvider(), LocalToolBackend(),
                                   self.artifacts, self._checkpointer, {})

    def close(self):
        try:
            self.long_tasks.stop_polling()
        except Exception:
            pass
        try:
            self.streams.close()
        except Exception:
            pass
        try:
            self._checkpoint_conn.close()
        except Exception:
            pass
        self.store.close()

    # ---------------------------------------------------------------- 对话
    def create_conversation(self, title=None, context=None):
        return self.store.create_conversation(title=title, context=context)

    def list_conversations(self):
        return self.store.list_conversations()

    def get_conversation(self, conversation_id):
        return self.store.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id):
        """删除一段对话及其全部节点 / 分支；返回是否删到（不存在即 False）。

        LangGraph 检查点表不删（留孤儿无害，见 `store.delete_conversation`）。
        """
        return self.store.delete_conversation(conversation_id)

    # ---------------------------------------------------------------- 推送流
    def open_stream(self, conversation_id, snapshot=True):
        """为某段对话开一条推送流（传输层无关；HTTP/SSE 端点阶段 6 复用）。

        snapshot=True 时，订阅方会先收到一条完整 `tools_snapshot`（T3.6）。
        """
        return self.streams.subscribe(conversation_id, snapshot=snapshot)

    def notify_tools_changed(self, conversation_id=None):
        """工具表发生变化时主动再推一次（T3.6；不传则推给所有已建立的流）。"""
        return self.streams.publish_tools(conversation_id)

    def _publisher(self, conversation_id, on_event):
        """造一个事件发布器：既进推送中枢，又（可选）回调给进程内消费者。"""
        hub = self.streams

        def publish(event_type, **data):
            event = hub.emit(conversation_id, event_type, data)
            if on_event is not None:
                on_event(event)
            return event

        return publish

    # ---------------------------------------------------------------- 上下文
    def recall_opts(self, conversation_id):
        """取回这段对话记住的运行口径快照（替代已删除的 session.py）。"""
        conv = self.store.get_conversation(conversation_id)
        if not conv:
            return {}
        return dict((conv.get("context") or {}).get("opts") or {})

    def remember_opts(self, conversation_id, opts):
        """记下本轮真实生效的口径（只对真正发起的任务调用）。"""
        opts = dict(opts or {})
        if not opts:
            return opts
        self.store.update_context(conversation_id, {"opts": opts})
        return opts

    def set_context(self, conversation_id, task_id=None, data_version=None):
        """设置对话上下文（当前任务编号 / 数据集版本）。"""
        patch = {}
        if task_id is not None:
            patch["task_id"] = task_id
        if data_version is not None:
            patch["data_version"] = data_version
        return self.store.update_context(conversation_id, patch)

    # ---------------------------------------------------------------- 读消息
    def get_messages(self, conversation_id):
        """读回**主支**检查点里累积的 LLM 上下文消息（重启不丢）。"""
        return self.get_thread_messages(conversation_id)

    def get_thread_messages(self, thread_id):
        """读回某条执行线程（主支或分支）检查点里累积的 LLM 上下文消息。"""
        state = self._reader.get_state(
            {"configurable": {"thread_id": thread_id}})
        values = state.values if state else None
        return list((values or {}).get("messages") or [])

    # ---------------------------------------------------------------- 读树/血缘
    def read_tree(self, conversation_id):
        """返回整棵**对话树**：所有节点（编号/父节点/角色/时间/摘要）+ 当前所在节点。

        阶段 7 起是真的树（一个节点可有多个子节点）；同时给出各分支与当前分支。
        """
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return None
        nodes = self.store.nodes_of(conversation_id)
        return {"conversation": conv,
                "nodes": nodes,
                "current_node_id": conv.get("current_node_id"),
                "current_branch": self._thread_for(conv),
                "branches": self.store.branches_of(conversation_id)}

    def ancestors(self, node_id):
        """从某节点回溯祖先链（根 → … → 直接父节点）。"""
        return self.store.ancestors(node_id)

    # ---------------------------------------------------------------- 分支
    def branch_from(self, conversation_id, from_node_id):
        """从对话中某个节点**新开一支**并把它设为当前所在（git 式）。

        - 旧后文（该节点原有的子链）**原样保留**，成为另一条支；
        - 新支有自己的 thread_id（独立检查点线程），后续 `run_turn` / `run_offline`
          挂在这条新支上，消息只进新支，**不污染主上下文**；
        - 返回分支记录与新的当前所在节点。
        """
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return {"ok": False, "error": {
                "code": "CONVERSATION_NOT_FOUND",
                "message": "对话不存在：%s" % conversation_id}}
        node = self.store.get_node(from_node_id)
        if node is None or node.get("conversation_id") != conversation_id:
            return {"ok": False, "error": {
                "code": "NODE_NOT_FOUND",
                "message": "节点不存在或不属于该对话：%s" % from_node_id}}
        branch = self.store.add_branch(
            conversation_id, node["id"], parent_thread=self._thread_for(conv))
        self.store.update_conversation(
            conversation_id, current_node_id=node["id"],
            current_branch=branch["thread_id"])
        return {"ok": True, "conversation_id": conversation_id,
                "branch": branch, "current_node_id": node["id"],
                "current_branch": branch["thread_id"]}

    def _thread_for(self, conv):
        """该对话**当前所在分支**的 thread_id（主支即对话编号）。"""
        if not conv:
            return None
        return conv.get("current_branch") or conv.get("id")

    def _branch_seed(self, conv, thread_id):
        """若当前分支线程还没有历史，就据分支点重建 LLM 上下文消息。

        返回 None 表示「本线程已有历史，无需预置」。分支首轮：优先用分支点节点
        记录的检查点回放**完整**消息（含工具往返）；没有检查点则用自建表里的
        节点内容重建（用户/助手/task 文本），保证新支接得上被选节点的上下文。
        """
        try:
            state = self._reader.get_state(
                {"configurable": {"thread_id": thread_id}})
        except Exception:
            state = None
        if state is not None and (state.values or {}).get("messages"):
            return None
        node_id = (conv or {}).get("current_node_id")
        if node_id is None:
            return []
        node = self.store.get_node(node_id)
        if node and node.get("checkpoint_id") and node.get("thread_id"):
            replay = self._checkpoint_messages(
                node["thread_id"], node["checkpoint_id"])
            if replay:
                return replay
        return self._ancestry_messages(node_id)

    def _checkpoint_messages(self, thread_id, checkpoint_id):
        """回放某线程某检查点上的消息列表（分支时预置新支上下文用）。"""
        try:
            state = self._reader.get_state({"configurable": {
                "thread_id": thread_id, "checkpoint_id": checkpoint_id}})
        except Exception:
            return []
        values = state.values if state else None
        return list((values or {}).get("messages") or [])

    def _ancestry_messages(self, node_id):
        """据自建表把「根 → 该节点」的内容重建成消息（检查点缺失时的兜底）。"""
        chain = list(self.store.ancestors(node_id))
        node = self.store.get_node(node_id)
        if node is not None:
            chain.append(node)
        out = []
        for item in chain:
            content = item.get("content") or ""
            if not content:
                continue
            if item.get("role") == "assistant":
                out.append(AIMessage(content=content))
            else:                                   # user / task 均按用户注入
                out.append(HumanMessage(content=content))
        return out

    def _record_checkpoint(self, graph, config, node_id, thread_id):
        """把本轮结束时的检查点编号记到助手节点上（异常也不影响回合结果）。"""
        try:
            snapshot = graph.get_state(config)
            cp = ((snapshot.config or {}).get("configurable", {})
                  .get("checkpoint_id")) if snapshot else None
            if cp:
                self.store.set_node_checkpoint(node_id, thread_id, cp)
        except Exception:
            pass

    # ---------------------------------------------------------------- 跑一轮
    def run_turn(self, conversation_id, user_message, provider=None,
                 tool_backend=None, facts=None, on_event=None, options=None):
        """跑一轮对话（同步）：返回最终回答与步骤，并把过程事件推进推送流。

        `on_event(event)` 是可选的进程内旁路回调（不订阅推送流也能拿到事件）；
        推送中枢始终会收到事件（无人订阅时静默丢弃）。
        `options` 是透传给模型请求的可选装载项（如 `reasoning_effort`）；缺省 None
        时行为与既有完全一致。
        """
        publish = self._publisher(conversation_id, on_event)
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return self._early_error(conversation_id, {
                "code": "CONVERSATION_NOT_FOUND",
                "message": "对话不存在：%s" % conversation_id}, reply="", publish=publish)

        try:
            active_provider = provider or self._provider_factory()
        except ModelError as exc:
            return self._early_error(conversation_id, exc.to_envelope()["error"],
                                     reply=exc.message, publish=publish)

        backend = self._wrap_backend(tool_backend or self._tool_backend,
                                     conversation_id)
        if facts is None:
            facts = self._facts(conversation_id)

        thread_id = self._thread_for(conv)
        user_node, assistant_node = self._open_turn_nodes(
            conversation_id, conv, user_message, publish, thread_id=thread_id)
        deadline = time.time() + MAX_LOOP_SECONDS
        graph = build_graph(
            active_provider, backend, self.artifacts, self._checkpointer, facts,
            on_delta=lambda text: publish(MESSAGE_DELTA, text=text,
                                          node_id=assistant_node["id"]),
            options=options)
        config = {"configurable": {"thread_id": thread_id}}
        seed = self._branch_seed(conv, thread_id)
        messages = (seed or []) + [HumanMessage(content=user_message)]
        try:
            result = graph.invoke(
                {"messages": messages,
                 "steps": [], "calls": 0, "plan_retries": 0, "final_retries": 0,
                 "deadline": deadline, "question": user_message,
                 "conversation_id": conversation_id,
                 "limit_reached": False, "blocked": None, "final": None,
                 "error": None},
                config)
            self._record_checkpoint(graph, config, assistant_node["id"], thread_id)
        except Exception as exc:
            return self._finalize_turn(conversation_id, self._finish_error(
                conversation_id, assistant_node, {
                    "code": "RUNTIME_ERROR",
                    "message": "运行时异常：%s" % exc}, [], publish), publish)
        return self._finalize_turn(conversation_id, self._finish_turn(
            conversation_id, user_node, assistant_node, result, publish), publish)

    async def run_turn_async(self, conversation_id, user_message, provider=None,
                             tool_backend=None, facts=None, on_event=None,
                             options=None):
        """跑一轮对话（异步）：用 AsyncSqliteSaver，不阻塞事件循环（T3.5）。

        LangGraph 会把同步的工具/模型节点放到线程池执行，因此事件循环不被占用，
        后台事件（阶段 4 的长任务状态）可与本轮流式输出并发推进同一条流。
        `options` 透传给模型请求（如 `reasoning_effort`）；缺省 None 行为不变。
        """
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        publish = self._publisher(conversation_id, on_event)
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return self._early_error(conversation_id, {
                "code": "CONVERSATION_NOT_FOUND",
                "message": "对话不存在：%s" % conversation_id}, reply="", publish=publish)

        try:
            active_provider = provider or self._provider_factory()
        except ModelError as exc:
            return self._early_error(conversation_id, exc.to_envelope()["error"],
                                     reply=exc.message, publish=publish)

        backend = self._wrap_backend(tool_backend or self._tool_backend,
                                     conversation_id)
        if facts is None:
            facts = self._facts(conversation_id)

        thread_id = self._thread_for(conv)
        user_node, assistant_node = self._open_turn_nodes(
            conversation_id, conv, user_message, publish, thread_id=thread_id)
        deadline = time.time() + MAX_LOOP_SECONDS
        config = {"configurable": {"thread_id": thread_id}}
        seed = self._branch_seed(conv, thread_id)
        messages = (seed or []) + [HumanMessage(content=user_message)]
        try:
            async with AsyncSqliteSaver.from_conn_string(self.db_path) as checkpointer:
                graph = build_graph(
                    active_provider, backend, self.artifacts, checkpointer, facts,
                    on_delta=lambda text: publish(MESSAGE_DELTA, text=text,
                                                  node_id=assistant_node["id"]),
                    options=options)
                result = await graph.ainvoke(
                    {"messages": messages,
                     "steps": [], "calls": 0, "plan_retries": 0, "final_retries": 0,
                     "deadline": deadline, "question": user_message,
                     "conversation_id": conversation_id,
                     "limit_reached": False, "blocked": None, "final": None,
                     "error": None},
                    config)
                self._record_checkpoint(graph, config, assistant_node["id"],
                                        thread_id)
        except Exception as exc:
            return self._finalize_turn(conversation_id, self._finish_error(
                conversation_id, assistant_node, {
                    "code": "RUNTIME_ERROR",
                    "message": "运行时异常：%s" % exc}, [], publish), publish)
        return self._finalize_turn(conversation_id, self._finish_turn(
            conversation_id, user_node, assistant_node, result, publish), publish)

    # ---------------------------------------------------------------- 离线直调
    def run_offline(self, conversation_id, tool_name, params=None,
                    tool_backend=None, facts=None, enforce_policy=True,
                    on_event=None):
        """离线直调一轮（阶段 5 T5.1 / T5.2）：工具名 + 可选参数 → **不经模型**执行。

        与 `run_turn` 同一套出口：同一张工具表（`tool_specs` / 工具表）、同一个
        执行后端（`tool_exec`，经 `_wrap_backend` 挂长任务装饰器）、同一条对话流。
        详情见 `runtime/offline.py`。
        """
        return run_offline(
            self, conversation_id, tool_name, params=params,
            tool_backend=tool_backend, facts=facts,
            enforce_policy=enforce_policy, on_event=on_event)

    # ---------------------------------------------------------------- 长任务续写
    def run_continuation(self, conversation_id, interrupt_text, meta=None,
                         provider=None, tool_backend=None, facts=None,
                         on_event=None, options=None):
        """长任务完成后的**续写轮**（阶段 4 T4.2）。

        与 `run_turn` 同构，区别在于触发者是「中断」而非用户：
          * 先落一个 role="task" 的事件节点（记下完成触发，父节点为当前节点），
            再落助手占位节点（续写文本流式挂它名下）；
          * 把中断文本作为一条 HumanMessage 注入**同一条对话**（同一 thread_id），
            模型据累积历史与结果摘要续写；
          * 流式增量与 message_done 推到**同一条对话流**，与既有事件有序共存。
        `options` 透传给模型请求（如 `reasoning_effort`）；缺省 None 行为不变。
        """
        publish = self._publisher(conversation_id, on_event)
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return self._early_error(conversation_id, {
                "code": "CONVERSATION_NOT_FOUND",
                "message": "对话不存在：%s" % conversation_id}, reply="", publish=publish)
        try:
            active_provider = provider or self._provider_factory()
        except ModelError:
            # 无可用模型（离线 / 未配置）：任务其实已跑完，不报错，改走确定性收尾。
            return self._degrade_continuation(
                conversation_id, conv, interrupt_text, meta, publish)

        backend = self._wrap_backend(tool_backend or self._tool_backend,
                                     conversation_id)
        if facts is None:
            facts = self._facts(conversation_id)

        thread_id = self._thread_for(conv)
        task_node = self.store.add_node(
            conversation_id, conv.get("current_node_id"), role="task",
            summary=summarize(interrupt_text), content=interrupt_text,
            thread_id=thread_id)
        publish(NODE_ADDED, **_node_payload(task_node))
        assistant_node = self.store.add_node(
            conversation_id, task_node["id"], role="assistant",
            summary="", content="", thread_id=thread_id)
        publish(NODE_ADDED, **_node_payload(assistant_node))

        deadline = time.time() + MAX_LOOP_SECONDS
        graph = build_graph(
            active_provider, backend, self.artifacts, self._checkpointer, facts,
            on_delta=lambda text: publish(MESSAGE_DELTA, text=text,
                                          node_id=assistant_node["id"]),
            options=options)
        config = {"configurable": {"thread_id": thread_id}}
        seed = self._branch_seed(conv, thread_id)
        messages = (seed or []) + [HumanMessage(content=interrupt_text)]
        try:
            result = graph.invoke(
                {"messages": messages,
                 "steps": [], "calls": 0, "plan_retries": 0, "final_retries": 0,
                 "deadline": deadline, "question": interrupt_text,
                 "conversation_id": conversation_id,
                 "limit_reached": False, "blocked": None, "final": None,
                 "error": None},
                config)
            self._record_checkpoint(graph, config, assistant_node["id"], thread_id)
        except Exception as exc:
            return self._finalize_turn(conversation_id, self._finish_error(
                conversation_id, assistant_node, {
                    "code": "RUNTIME_ERROR",
                    "message": "运行时异常：%s" % exc}, [], publish), publish)
        return self._finalize_turn(conversation_id, self._finish_turn(
            conversation_id, task_node, assistant_node, result, publish), publish)

    def _degrade_continuation(self, conversation_id, conv, interrupt_text, meta,
                              publish):
        """无可用模型时的**确定性收尾**（不报错）：落节点、推助手消息、收产物。

        长任务其实已完成，只是没配模型、无法「注入结果让模型续写」。此时按长任务
        视图与任务目录下 `result.json` 的真实状态 / 计数 / 五维分拼一句人话作为助手
        消息（`message_delta` / `message_done`）；`result_payload` 由完成路径
        （`LongTaskManager.finalize`）照常推；产物在 `_finalize_turn` 里照常收集。
        """
        thread_id = self._thread_for(conv)
        task_node = self.store.add_node(
            conversation_id, conv.get("current_node_id"), role="task",
            summary=summarize(interrupt_text), content=interrupt_text,
            thread_id=thread_id)
        publish(NODE_ADDED, **_node_payload(task_node))
        assistant_node = self.store.add_node(
            conversation_id, task_node["id"], role="assistant",
            summary="", content="", thread_id=thread_id)
        publish(NODE_ADDED, **_node_payload(assistant_node))

        result_data = artifacts_ref.read_task_result(
            (meta or {}).get("hadoop_task_id"))
        text = completion_message(meta, result_data)
        for piece in explain.chunk_text(text):
            publish(MESSAGE_DELTA, text=piece, node_id=assistant_node["id"])
        assistant_node = self.store.update_node(
            assistant_node["id"], summary=summarize(text), content=text)
        self.store.update_conversation(
            conversation_id, current_node_id=assistant_node["id"])
        publish(MESSAGE_DONE, node_id=assistant_node["id"], text=text)
        result = {"ok": True, "engine": "runtime", "degraded": True,
                  "conversation_id": conversation_id,
                  "user_node_id": task_node["id"],
                  "node_id": assistant_node["id"],
                  "final": text, "reply": text, "steps": [],
                  "task_id": (meta or {}).get("hadoop_task_id"),
                  "task_started": False, "error": None,
                  "llm": {"used": False, "steps": [],
                          "note": "无可用模型，按长任务真实结果做确定性收尾"}}
        return self._finalize_turn(conversation_id, result, publish)

    def reconcile_long_tasks(self, probe):
        """重启后按执行侧核对残留长任务（阶段 4 T4.3；probe 为可注入核对接口）。"""
        return self.long_tasks.reconcile(probe)

    def poll_long_tasks(self, probe, provider=None, facts=None):
        """周期巡检执行侧终态并唤醒续写（阶段 4 T4.2 的完成检测）。"""
        return self.long_tasks.poll(probe, provider=provider, facts=facts)

    # ---------------------------------------------------------------- 内部
    def _wrap_backend(self, backend, conversation_id):
        """给执行后端套一层长任务装饰器（阶段 4 T4.1；不改变上层调用形状）。"""
        if self.long_tasks is None:
            return backend
        return LongTaskBackend(backend, self.long_tasks, conversation_id)

    def _with_long_tasks(self, conversation_id, result):
        """把该对话的长任务视图并入回合结果（用户/前端可立即查询）。"""
        try:
            result["long_tasks"] = [task_view(t)
                                    for t in self.long_tasks.tasks_of(conversation_id)]
        except Exception:
            result["long_tasks"] = []
        return result

    # ---------------------------------------------------------------- 产物引用
    def _finalize_turn(self, conversation_id, result, publish,
                       extra_task_ids=None, extra_args_list=None):
        """回合收尾（四条路径共用）：并入长任务视图，再收集产物引用。

        产物引用（接口文档 §3.12）：只对**新增**产物发 `artifact_added` 事件，
        并把**完整列表**放进回合信封的 `artifacts` 字段（加性），同时持久化进对话
        上下文（`context_json`），供刷新 / 切换对话恢复。
        """
        self._with_long_tasks(conversation_id, result)
        return self._collect_artifacts(
            conversation_id, publish, result,
            extra_task_ids=extra_task_ids, extra_args_list=extra_args_list)

    def _gather_artifact_refs(self, conversation_id, extra_task_ids=None,
                              extra_args_list=None):
        """收集这一段对话到目前为止可用的产物引用。

        返回 `(完整列表, 新增列表, 任务编号列表)`；任务目录 / 方案文件不存在就跳过。
        """
        conv = self.store.get_conversation(conversation_id) or {}
        context = conv.get("context") or {}
        long_tasks = context.get("long_tasks") or {}
        task_ids = artifacts_ref.task_ids_of(context, long_tasks,
                                             extra=extra_task_ids)
        schemes = artifacts_ref.scheme_names_of(
            context.get("opts"), long_tasks, extra_args_list,
            has_tasks=bool(task_ids))
        items = []
        for task_id in task_ids:
            items.extend(artifacts_ref.collect_task_artifacts(task_id))
        items.extend(artifacts_ref.collect_scheme_artifacts(schemes))
        incoming = artifacts_ref.dedupe(items)
        # 防御性再过滤：旧对话可能在过滤规则上线前就把内部文件存进了产物列表。
        known = artifacts_ref.strip_internal(context.get("artifacts"))
        known_ids = set(a.get("id") for a in known)
        full, _ = artifacts_ref.merge(known, incoming)
        # 同名产物去歧义：不同任务的同名文件在标题上补短任务号后缀。
        full = artifacts_ref.disambiguate_titles(full)
        added = [a for a in full if a.get("id") not in known_ids]
        return full, added, task_ids

    def visible_artifacts(self, conversation_id):
        """恢复某对话的产物列表（供 `GET …/artifacts`）：过滤内部文件 + 同名去歧义。

        对**持久化列表**再过滤一次，保证老对话里已存在的 `driver.log` 等不再出现；
        对话不存在返回 None。
        """
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            return None
        raw = (conv.get("context") or {}).get("artifacts") or []
        return artifacts_ref.disambiguate_titles(artifacts_ref.strip_internal(raw))

    def _collect_artifacts(self, conversation_id, publish, result,
                           extra_task_ids=None, extra_args_list=None):
        """把产物引用并入回合结果、发新增事件、持久化完整列表。"""
        try:
            full, added, task_ids = self._gather_artifact_refs(
                conversation_id, extra_task_ids=extra_task_ids,
                extra_args_list=extra_args_list)
        except Exception:                              # 收集失败不影响回合
            result.setdefault("artifacts", [])
            return result
        for artifact in added:
            publish(ARTIFACT_ADDED, artifact=artifact)
        patch = {"artifacts": full}
        if task_ids:
            patch["task_ids"] = task_ids
        try:
            self.store.update_context(conversation_id, patch)
        except Exception:
            pass
        result["artifacts"] = full
        return result

    def _facts(self, conversation_id):
        try:
            return self._facts_factory()
        except Exception:
            return {}

    def _open_turn_nodes(self, conversation_id, conv, user_message, publish,
                         thread_id=None):
        """建立本轮节点：先落用户节点，再落助手节点占位（流式文本挂它名下）。

        助手节点先建占位、回合结束再回填内容 —— 这样 `message_delta` 才能带上
        所属节点编号（接口文档 §3.6）；节点数量、父子关系与阶段 3a 完全一致。
        父节点取对话的当前所在节点：分支后即分支点节点，故新支挂在新支下，
        旧后文（该节点原存的子链）不受影响。
        """
        user_node = self.store.add_node(
            conversation_id, conv.get("current_node_id"), role="user",
            summary=summarize(user_message), content=user_message,
            thread_id=thread_id)
        publish(NODE_ADDED, **_node_payload(user_node))
        assistant_node = self.store.add_node(
            conversation_id, user_node["id"], role="assistant",
            summary="", content="", thread_id=thread_id)
        publish(NODE_ADDED, **_node_payload(assistant_node))
        return user_node, assistant_node

    def _finish_turn(self, conversation_id, user_node, assistant_node, result,
                     publish):
        """回合结束：错误/上限/拦截转入 _finish_error，否则回填并推 message_done。"""
        steps = list(result.get("steps") or [])
        error = result.get("error")
        if error:
            return self._finish_error(conversation_id, assistant_node, error,
                                      steps, publish)
        if result.get("limit_reached"):
            return self._finish_error(conversation_id, assistant_node, {
                "code": "LOOP_LIMIT",
                "message": "达到循环上限（步数 %d 或时长 %d 秒）"
                           % (len(steps), MAX_LOOP_SECONDS)}, steps, publish)
        if result.get("blocked"):
            return self._finish_error(conversation_id, assistant_node, {
                "code": "FABRICATED_NUMBER", "message": result["blocked"]},
                steps, publish)

        final_text = result.get("final") or ""
        info_steps = [{"tool": note.get("tool"), "ok": note.get("ok"),
                       "envelope": envelope_of(note, self.artifacts)}
                      for note in steps]
        task_id, started = protocol.task_info({"steps": info_steps})
        assistant_node = self.store.update_node(
            assistant_node["id"], summary=summarize(final_text), content=final_text)
        patch = {}
        if started and task_id:
            patch["task_id"] = task_id
        if patch:
            self.store.update_context(conversation_id, patch)
        self.store.update_conversation(
            conversation_id, current_node_id=assistant_node["id"])
        publish(MESSAGE_DONE, node_id=assistant_node["id"], text=final_text)
        return {"ok": True, "engine": "llm",
                "conversation_id": conversation_id,
                "user_node_id": user_node["id"], "node_id": assistant_node["id"],
                "final": final_text, "reply": final_text,
                "steps": steps, "task_id": task_id,
                "task_started": bool(started),
                "error": None,
                "llm": {"used": True, "steps": steps,
                        "note": "由大模型编排 %d 步工具调用，数字均来自工具结果"
                                % len(steps)}}

    def _finish_error(self, conversation_id, assistant_node, error, steps=None,
                      publish=None):
        """错误也把占位助手节点回填成值守内容，并推一条 error 事件。"""
        steps = list(steps or [])
        message = (error or {}).get("message") or "模型未给出有效回答"
        assistant_node = self.store.update_node(
            assistant_node["id"], summary=summarize(message), content=message)
        self.store.update_conversation(
            conversation_id, current_node_id=assistant_node["id"])
        if publish is not None:
            publish(ERROR, error=error or {"code": "MODEL_ERROR",
                                           "message": message},
                    node_id=assistant_node["id"])
        return {"ok": False, "engine": "llm",
                "conversation_id": conversation_id,
                "user_node_id": assistant_node["parent_id"],
                "node_id": assistant_node["id"],
                "final": None, "reply": message, "steps": steps,
                "task_id": None, "task_started": False,
                "error": error,
                "llm": {"used": True, "steps": steps, "note": message}}

    def _early_error(self, conversation_id, error, reply=None, publish=None):
        """开跑前的失败（对话不存在 / 模型未配置）：不落节点，如实报错。"""
        if publish is not None:
            publish(ERROR, error=error)
        return {"ok": False, "engine": "llm",
                "conversation_id": conversation_id, "steps": [],
                "final": None, "reply": (error or {}).get("message") if reply is None else reply,
                "error": error}
