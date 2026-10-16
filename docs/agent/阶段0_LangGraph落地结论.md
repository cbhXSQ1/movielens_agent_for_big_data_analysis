# 阶段 0 · LangGraph 落地结论

> 执行子智能体：`ag-env`。范围仅限 `T0.1`–`T0.4`，不涉及后续阶段。
> 结论基于本机真实运行输出，未调用任何真实大模型网关（无 API key）。

---

## 1. 可用性结论（TL;DR）

- **LangGraph 在本机 Windows 环境可用。** 预览版图能正常编译、执行。
- **流式可用**：既能在 `messages` 模式下逐 token 产出（假模型），也能在 `custom` 模式下由节点主动推送增量事件。实测一次输出 9 个增量片段。
- **持久化可用**：用 SQLite 检查点后端，**两次独立进程**运行之间状态不丢；第二次进程读到了第一次保存的历史。
- **Python 版本**：实测使用 **Python 3.14.6**（仓库内隔离虚拟环境）。
- **未验证项**：真实模型网关（受约束，不做）、多步工具调用循环（属阶段 3）、异步运行时压测。

---

## 2. 环境与确切版本（T0.1）

### 2.1 虚拟环境

- 位置：`E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe\.venv`
  （`.scratch/` 已被 `.gitignore:19` 忽略，`git check-ignore` 已验证该目录不会入库。）
- 解释器：`Python 3.14.6 (tags/v3.14.6:c63aec6, Jun 10 2026) [MSC v.1944 64 bit (AMD64)]`
- 操作系统：`Windows-11-10.0.26200-SP0`

### 2.2 关键依赖版本（`importlib.metadata` 实测）

| 包 | 版本 |
|---|---|
| `langgraph` | **1.2.15** |
| `langgraph-checkpoint` | **4.3.0** |
| `langgraph-checkpoint-sqlite` | **3.1.2** |
| `langchain-core` | **1.6.11** |
| `langgraph-prebuilt` | 1.1.1 |
| `langgraph-sdk` | 0.4.7 |
| `pydantic` | 2.14.1 |
| `aiosqlite` | 0.22.1 |
| `sqlite-vec` | 0.1.9 |

完整清单（41 行，`pip freeze`）已存为证据文件：
`.scratch\langgraph-probe\pip-freeze.txt`。

### 2.3 安装命令（可重复执行）

```powershell
cd 'E:\学\大数据分析\movielens_agent_for_big_data_analysis'
python -m venv '.scratch\langgraph-probe\.venv'
& '.scratch\langgraph-probe\.venv\Scripts\python.exe' -m pip install langgraph langgraph-checkpoint-sqlite
```

安装后版本检查的真实输出：

```
langgraph==1.2.15
langgraph-checkpoint==4.3.0
langgraph-checkpoint-sqlite==3.1.2
langchain-core==1.6.11
langgraph-prebuilt==1.1.1
langgraph-sdk==0.4.7
pydantic==2.14.1
```

---

## 3. 样例与运行方式

所有样例位于 `.scratch\langgraph-probe\`（已被 git 忽略）：

| 文件 | 对应任务 | 作用 |
|---|---|---|
| `stream_probe.py` | T0.2 | 假流式模型 + 节点推送，验证增量输出 |
| `persistence_probe.py` | T0.3 | SQLite 检查点，验证跨进程恢复 |
| `pip-freeze.txt` | T0.1 | 版本证据 |
| `checkpoints.sqlite` | T0.3 | 运行期生成的检查点库（可删） |

运行（PowerShell，`PYTHONIOENCODING=utf-8` 避免中文/重定向乱码）：

```powershell
cd 'E:\学\大数据分析\movielens_agent_for_big_data_analysis\.scratch\langgraph-probe'
$env:PYTHONIOENCODING='utf-8'
& '.venv\Scripts\python.exe' stream_probe.py

# 持久化：先清库，再分两次「独立进程」运行
Remove-Item -LiteralPath 'checkpoints.sqlite' -ErrorAction SilentlyContinue
& '.venv\Scripts\python.exe' persistence_probe.py first
& '.venv\Scripts\python.exe' persistence_probe.py second
& '.venv\Scripts\python.exe' persistence_probe.py state
```

脚本内部没有网络调用；`FakeStreamingChatModel` 继承 `BaseChatModel`，只实现 `_generate` 与 `_stream`，逐片段 `yield AIMessageChunk`，等价于真实 LLM 的逐 token 输出。

---

## 4. 验证证据（真实输出）

### 4.1 流式：确实产生多个增量片段（T0.2）

命令 `& '.venv\Scripts\python.exe' stream_probe.py` 的实际输出：

```
=== Demo A: stream_mode='messages' (fake LLM token stream) ===
  chunk #1: 'Hello'
  chunk #2: ', '
  chunk #3: 'this '
  chunk #4: 'is '
  chunk #5: 'a '
  chunk #6: 'streamed '
  chunk #7: 'reply'
  chunk #8: '.'
  chunk #9: ''
[Demo A] total incremental chunks = 9

=== Demo B: stream_mode='custom' (node writer) ===
  payload #1: {'seq': 1, 'piece': 'alpha'}
  payload #2: {'seq': 2, 'piece': 'beta'}
  payload #3: {'seq': 3, 'piece': 'gamma'}
  payload #4: {'seq': 4, 'piece': 'delta'}
[Demo B] total incremental payloads = 4

RESULT: incremental streaming detected = True (A=9, B=4)
```

说明：Demo A 的 9 个片段 = 8 个内容片段 + 1 个流结束时的空 `AIMessageChunk`（框架正常行为，做拼接时空内容可忽略）。Demo B 证明节点可经 `get_stream_writer()` 主动推自定义事件——这正是接口文档 §3.6 各类事件（`message_delta` / `task_status` / `result_payload` …）的落点。

### 4.2 持久化：第二次进程读到第一次的状态（T0.3）

三次**独立进程**的实际输出：

```
=== RUN 'first' (db=checkpoints.sqlite) ===
  [pre ] saved state before invoke = {}
  [node] history visible on entry = ['input:first']
  [node] appending = 'turn:1'
  [post] saved state after invoke  = {'history': ['input:first', 'turn:1']}
```

```
=== RUN 'second' (db=checkpoints.sqlite) ===
  [pre ] saved state before invoke = {'history': ['input:first', 'turn:1']}
  [node] history visible on entry = ['input:first', 'turn:1', 'input:second']
  [node] appending = 'turn:2'
  [post] saved state after invoke  = {'history': ['input:first', 'turn:1', 'input:second', 'turn:2']}
```

```
=== STATE DUMP (db=checkpoints.sqlite) ===
  values = {'history': ['input:first', 'turn:1', 'input:second', 'turn:2']}
  history length = 4
```

关键点：`second` 进程在 `invoke` 之前 `get_state` 就已经读到 `turn:1`，证明**进程重启后状态不丢**。`thread_id = "probe-thread-1"` 即「一段对话」的持久化单元。

---

## 5. 局限与坑

### 5.1 Windows / PowerShell 特有

1. **pip 的 stderr 提示会被 PowerShell 渲染成红色 `NativeCommandError`。** 安装其实成功（`Successfully installed ...`），结尾那段 `python.exe : ... CategoryInfo ...` 只是 pip 的升级提示走了 stderr。**不是失败**，可忽略；想消除可加 `2>&1`。
2. **控制台编码 GBK，中文输出可能乱码。** 运行前设 `$env:PYTHONIOENCODING='utf-8'`。脚本内部建议只用 ASCII 输出，避免证据被乱码污染（本样例即如此）。
3. **仓库路径含非 ASCII（`E:\学\大数据分析`）。** 本次 venv/pip 能正常工作，但 pip 提示行里该路径显示为乱码，说明部分工具对非 ASCII 路径处理不干净。建议**产物文件名保持 ASCII**，风险最小。
4. **`Activate.ps1` 可能被执行策略拦截。** 本报告一律**直接调用 `.venv\Scripts\python.exe`**，不依赖激活，天然规避。
5. **Windows 无 `fork`。** 若后续运行时用后台线程/子进程跑检查点，SQLite 连接需 `check_same_thread=False`，并注意进程退出前 `close()`，否则可能 `database is locked`。
6. **长驻服务建议开 WAL**：`PRAGMA journal_mode=WAL`，避免读写互锁。

### 5.2 框架相关

1. **检查点状态必须可序列化。** LangGraph 默认用 msgpack 序列化整份图状态；状态里放**不可序列化的大对象（DataFrame、文件句柄、连接）会报错或撑爆存储**。应只放引用/编号，数据本体放外部（对应接口文档「结果板块」独立取用）。
2. **`SqliteSaver` 是同步阻塞实现**；异步运行时（阶段 3）应改用 `AsyncSqliteSaver`（`aiosqlite` 已随依赖装好）。
3. **检查点库会随对话增长**：每个 super-step 落一份快照。长期运行需评估清理/归档策略。
4. **LangGraph 1.x 迭代快，版本语义有破坏性变更。** 本次锁到 `1.2.15`，后续升级前先冻结版本再验证。
5. 只有 `compile(checkpointer=...)` 之后才具备持久化与 `interrupt()` 能力；无检查点时 `interrupt` 不可用。

### 5.3 未覆盖

- 真实模型网关（无 key，按约束不测）。
- 多步工具调用循环、异步并发、`interrupt` 唤醒——分别属阶段 3、4。

---

## 6. 给后续阶段的注意事项与推荐配置

1. **对话 == `thread_id`。** 每个 `conversation` 用一个稳定 `thread_id`，直接映射接口文档 §2「对话」，无需另造概念。
2. **推送流机制已就绪，按需组合 stream mode。** 用 `stream_mode=["messages","custom","updates"]` 得到 `(mode, payload)` 元组：`messages` 出模型 token（→ `message_delta`），`custom`（配 `get_stream_writer()` / 异步 `aget_stream_writer()`）出业务事件（`tools_snapshot` / `task_status` / `result_payload` / `node_added` / `error`）。**阶段 3 的「每对话一条推送流」直接建在这上面。**
3. **消息用 reducer 累积。** 会话状态用 `Annotated[list, add_messages]`，检查点会自然累积历史；这也是阶段 3 对话持久化的底座。
4. **阶段 7 对话树不要只靠检查点。** 检查点是「每条 thread 一条线性执行日志，带父检查点 id」，与规划里 **git 式独立分支、旧支保留** 不完全等价（尤其「分支不污染主上下文」要求）。建议：**检查点当执行日志，权威对话树（节点/父子/分支）另建自有表**（落在同一 SQLite 或独立表），阶段 3 的 T3.1/T3.2 据此刻画。
5. **长任务唤醒（阶段 4）优先用内置 `interrupt()` + `Command(resume=...)`**，它天然要求检查点、天然支持 `task_resumed` 语义；不要把唤醒逻辑重新发明一遍。
6. **测试坚持假模型。** 本样例的 `FakeStreamingChatModel` 可直接演进为贯穿任务的 TX.1 脚手架（可控、可重放、不触网）。
7. **虚拟环境放在 `.scratch/venv`（或本报告用的 `.scratch/langgraph-probe/.venv`）。** 若要改用仓库根 `.venv/`，**必须先把 `.venv/` 加进 `.gitignore`**——当前 `.gitignore` 只忽略了 `.scratch/`，未忽略 `.venv/`。
8. **建议的锁定依赖（可直接写入 `requirements.txt`）：**
   ```
   langgraph==1.2.15
   langgraph-checkpoint-sqlite==3.1.2
   langchain-core==1.6.11
   ```
   （完整传递依赖见 `pip-freeze.txt`；每次升级后重新冻结。）
9. **Python 版本：本机 3.14.6 可用。** 若后续引入的第三方模型 SDK 在 3.14 上缺 wheel，本机另有 3.13 / 3.11 / 3.10 可回退；选更低版本时需重跑本报告全部样例以确认行为一致。

---

## 7. 交付物清单

- `docs/agent/阶段0_LangGraph落地结论.md`（本文件，入库）
- `.scratch/langgraph-probe/stream_probe.py`（忽略）
- `.scratch/langgraph-probe/persistence_probe.py`（忽略）
- `.scratch/langgraph-probe/pip-freeze.txt`（忽略）
- `.scratch/langgraph-probe/.venv/`（忽略）
- `.scratch/langgraph-probe/checkpoints.sqlite`（忽略，运行期生成）

> 注意：样例代码在 `.scratch/` 下，**不会入库**。若团队要长期保留样例，需将其迁到受版本控制的目录；在此之前，本文件第 3 节命令足以复现全部验证。
