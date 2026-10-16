# -*- coding: utf-8 -*-
"""阶段 3a（ag-runtime）· T3.1 / T3.2：对话与节点的**权威**持久化层（SQLite）。

为什么自建表、不只靠 LangGraph 检查点（阶段 0 结论 §6.4）：
  检查点是「每条 thread 一条线性执行日志」，而对话要成**树**（阶段 7 分支/跳回）。
  因此这里另立权威表：
    conversations  对话（编号、标题、时间、当前所在节点、上下文）
    nodes          回合节点（编号、父节点、角色、时间、摘要、内容）
  检查点只当**执行日志 / LLM 上下文消息**用；血缘以本表为准。

数据模型
--------
对话 conversation：有编号（`c0001`…）、标题、创建/更新时间、当前所在节点、
  上下文（当前任务编号、数据集版本、以及直调口径快照 opts）。
节点 node：有编号（自增整数）、**父节点**（parent_id，根为 None）、角色
  （user / assistant）、创建时间、可读摘要、内容（大内容走 content_ref 外置）。

血缘：**有向树**（parent_id 串起来，一个节点可有多个子节点）。`ancestors()` 从任意
节点回溯到根。阶段 7 起：
  * 一个节点可挂多条子链 —— 从某节点「继续」即在其下新开一支，旧后文原样保留；
  * `branches` 表登记每条分支（分支点节点、分支专属 thread_id、派生自哪条线程）；
  * 对话的 `current_branch` 记当前所在分支的 thread_id（NULL 即主支 = 对话编号）；
  * 节点上的 `thread_id` / `checkpoint_id` 是「该节点所处执行线程 + 其检查点」的回溯线索。

分工：**血缘以本表为权威**（节点 / 父子 / 分支），检查点（LangGraph）只当执行日志与
LLM 上下文消息的载体；分支的消息回溯在 Runtime 里据 `thread_id` / `checkpoint_id` 完成。

连接策略：独立连接 + WAL，Windows 无 fork 也安全；写操作加锁，避免
ThreadingHTTPServer 并发时 `database is locked`。
"""

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    title           TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    current_node_id INTEGER,
    context_json    TEXT,
    current_branch  TEXT
);
CREATE TABLE IF NOT EXISTS nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    parent_id       INTEGER,
    role            TEXT NOT NULL,
    summary         TEXT,
    content         TEXT,
    content_ref     TEXT,
    created_at      REAL NOT NULL,
    seq             INTEGER NOT NULL,
    thread_id       TEXT,
    checkpoint_id   TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_conv ON nodes(conversation_id, seq);
CREATE TABLE IF NOT EXISTS branches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    from_node_id    INTEGER NOT NULL,
    thread_id       TEXT NOT NULL,
    parent_thread   TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_branches_conv ON branches(conversation_id);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

#: 老库升级：新增列（表 / 列 / 声明）。`CREATE TABLE IF NOT EXISTS` 不会给已存在的表
#: 补列，故对既有 SQLite 文件显式 ALTER。新库建表时已含这些列，ALTER 会跳过。
_COLUMN_MIGRATIONS = (
    ("conversations", "current_branch", "TEXT"),
    ("nodes", "thread_id", "TEXT"),
    ("nodes", "checkpoint_id", "TEXT"),
)


def open_connection(db_path):
    """打开一个（进程内可跨线程用的）SQLite 连接，并开 WAL。"""
    directory = os.path.dirname(os.path.abspath(db_path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    return conn


def summarize(text, limit=80):
    """把一段文本压成一行人话摘要（取首个非空行，超长截断）。"""
    if not text:
        return ""
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            return line[:limit] + ("…" if len(line) > limit else "")
    return ""


class ConversationStore(object):
    """对话与节点的权威存储。普通类、显式 SQL；不依赖任何第三方库。"""

    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = open_connection(db_path)
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self):
        """老库补列（阶段 7：分支所需的 current_branch / thread_id / checkpoint_id）。"""
        for table, column, decl in _COLUMN_MIGRATIONS:
            rows = self._conn.execute(
                "PRAGMA table_info(%s)" % table).fetchall()
            existing = {row[1] for row in rows}
            if column not in existing:
                self._conn.execute(
                    "ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ---------------------------------------------------------------- 对话
    def _next_counter(self, key):
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        n = (int(row[0]) if row and row[0] else 0) + 1
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(n)))
        return n

    def create_conversation(self, title=None, context=None):
        """新建一段对话，返回它的编号（`c0001` 形式）。"""
        with self._lock:
            n = self._next_counter("conversation_seq")
            cid = "c%04d" % n
            now = time.time()
            self._conn.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at, "
                "current_node_id, context_json) VALUES(?, ?, ?, ?, ?, ?)",
                (cid, title or "", now, now, None,
                 json.dumps(context or {}, ensure_ascii=False)))
            self._conn.commit()
            return cid

    def _row_to_conversation(self, row):
        if row is None:
            return None
        context = {}
        if row[5]:
            try:
                context = json.loads(row[5]) or {}
            except ValueError:
                context = {}
        return {"id": row[0], "title": row[1], "created_at": row[2],
                "updated_at": row[3], "current_node_id": row[4],
                "context": context, "current_branch": row[6]}

    def get_conversation(self, conversation_id):
        # 连接跨线程共享：读也必须持锁（阶段 3b 并发两条对话时暴露的竞态，
        # 同连接的并发游标会触发 sqlite3.InterfaceError / 读到不完整结果）。
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, created_at, updated_at, current_node_id, "
                "context_json, current_branch FROM conversations WHERE id = ?",
                (conversation_id,)).fetchone()
            return self._row_to_conversation(row)

    def list_conversations(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at, updated_at, current_node_id, "
                "context_json, current_branch FROM conversations "
                "ORDER BY created_at DESC").fetchall()
            return [self._row_to_conversation(r) for r in rows]

    def update_conversation(self, conversation_id, current_node_id=None,
                            context=None, current_branch=None, touch=True):
        """更新对话：当前所在节点 / 上下文（整体替换）/ 当前分支（thread_id）。"""
        with self._lock:
            if context is not None:
                self._conn.execute(
                    "UPDATE conversations SET context_json = ? WHERE id = ?",
                    (json.dumps(context, ensure_ascii=False), conversation_id))
            if current_node_id is not None:
                self._conn.execute(
                    "UPDATE conversations SET current_node_id = ? WHERE id = ?",
                    (current_node_id, conversation_id))
            if current_branch is not None:
                self._conn.execute(
                    "UPDATE conversations SET current_branch = ? WHERE id = ?",
                    (current_branch, conversation_id))
            if touch:
                self._conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (time.time(), conversation_id))
            self._conn.commit()

    def update_context(self, conversation_id, patch):
        """合并式更新上下文（只覆盖 patch 里的键）。返回合并后的上下文。"""
        with self._lock:
            conv = self.get_conversation(conversation_id)
            context = dict(conv.get("context") or {}) if conv else {}
            for key, value in (patch or {}).items():
                if value is None:
                    context.pop(key, None)
                else:
                    context[key] = value
            self.update_conversation(conversation_id, context=context)
            return context

    # ---------------------------------------------------------------- 节点
    _NODE_COLUMNS = ("id, conversation_id, parent_id, role, summary, content, "
                     "content_ref, created_at, seq, thread_id, checkpoint_id")

    def _row_to_node(self, row):
        if row is None:
            return None
        return {"id": row[0], "conversation_id": row[1], "parent_id": row[2],
                "role": row[3], "summary": row[4], "content": row[5],
                "content_ref": row[6], "created_at": row[7], "seq": row[8],
                "thread_id": row[9], "checkpoint_id": row[10]}

    def add_node(self, conversation_id, parent_id, role, summary="",
                 content="", content_ref=None, thread_id=None,
                 checkpoint_id=None):
        """新增一个回合节点。parent_id 为该对话内父节点的编号（根传 None）。

        `thread_id` 记该节点所处的执行线程（分支隔离用；主支即对话编号）。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM nodes WHERE conversation_id = ?",
                (conversation_id,)).fetchone()
            seq = int(row[0]) + 1
            cur = self._conn.execute(
                "INSERT INTO nodes(conversation_id, parent_id, role, summary, "
                "content, content_ref, created_at, seq, thread_id, checkpoint_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (conversation_id, parent_id, role, summary or "", content or "",
                 content_ref, time.time(), seq, thread_id, checkpoint_id))
            self._conn.commit()
            return self.get_node(cur.lastrowid)

    def set_node_checkpoint(self, node_id, thread_id, checkpoint_id):
        """记下某节点对应的执行线程与检查点编号（供分支时回溯 LLM 上下文）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET thread_id = ?, checkpoint_id = ? WHERE id = ?",
                (thread_id, checkpoint_id, node_id))
            self._conn.commit()
            return self.get_node(node_id)

    def update_node(self, node_id, summary=None, content=None, content_ref=None):
        """回填一个节点的摘要/内容（阶段 3b 流式：先建节点占位，稍后补内容）。"""
        with self._lock:
            if summary is not None:
                self._conn.execute(
                    "UPDATE nodes SET summary = ? WHERE id = ?", (summary, node_id))
            if content is not None:
                self._conn.execute(
                    "UPDATE nodes SET content = ? WHERE id = ?", (content, node_id))
            if content_ref is not None:
                self._conn.execute(
                    "UPDATE nodes SET content_ref = ? WHERE id = ?",
                    (content_ref, node_id))
            self._conn.commit()
            return self.get_node(node_id)

    def get_node(self, node_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT %s FROM nodes WHERE id = ?" % self._NODE_COLUMNS,
                (node_id,)).fetchone()
            return self._row_to_node(row)

    def nodes_of(self, conversation_id):
        with self._lock:
            rows = self._conn.execute(
                "SELECT %s FROM nodes WHERE conversation_id = ? ORDER BY seq"
                % self._NODE_COLUMNS, (conversation_id,)).fetchall()
            return [self._row_to_node(r) for r in rows]

    # ---------------------------------------------------------------- 分支
    def _row_to_branch(self, row):
        if row is None:
            return None
        return {"id": row[0], "conversation_id": row[1],
                "from_node_id": row[2], "thread_id": row[3],
                "parent_thread": row[4], "created_at": row[5]}

    _BRANCH_COLUMNS = ("id, conversation_id, from_node_id, thread_id, "
                       "parent_thread, created_at")

    def add_branch(self, conversation_id, from_node_id, parent_thread=None):
        """登记一条从 `from_node_id` 另起的分支，返回分支记录（含专属 thread_id）。

        thread_id 形如 `<对话编号>:b<n>`：与主支（thread_id == 对话编号）区分开，
        分支的消息因此落在**独立检查点线程**里，不污染主上下文。
        """
        with self._lock:
            n = self._next_counter("branch_seq")
            thread_id = "%s:b%d" % (conversation_id, n)
            cur = self._conn.execute(
                "INSERT INTO branches(conversation_id, from_node_id, thread_id, "
                "parent_thread, created_at) VALUES(?,?,?,?,?)",
                (conversation_id, from_node_id, thread_id, parent_thread,
                 time.time()))
            self._conn.commit()
            return self.get_branch(cur.lastrowid)

    def get_branch(self, branch_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT %s FROM branches WHERE id = ?" % self._BRANCH_COLUMNS,
                (branch_id,)).fetchone()
            return self._row_to_branch(row)

    def branches_of(self, conversation_id):
        with self._lock:
            rows = self._conn.execute(
                "SELECT %s FROM branches WHERE conversation_id = ? ORDER BY id"
                % self._BRANCH_COLUMNS, (conversation_id,)).fetchall()
            return [self._row_to_branch(r) for r in rows]

    def ancestors(self, node_id):
        """从某节点回溯祖先链：返回根 → … → 直接父节点（不含自身）的列表。"""
        chain = []
        node = self.get_node(node_id)
        seen = set()
        while node and node.get("parent_id") is not None:
            parent_id = node["parent_id"]
            if parent_id in seen:           # 防御：表被写坏时不死循环
                break
            seen.add(parent_id)
            node = self.get_node(parent_id)
            if node:
                chain.append(node)
        chain.reverse()
        return chain
