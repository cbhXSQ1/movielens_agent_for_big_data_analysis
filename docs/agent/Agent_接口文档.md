# Agent 接口文档（迭代二）

> 状态：初稿。本文只覆盖 Agent 这一部分的接口，分两块：
> ① 前端 ↔ Agent；② Agent ↔ Hadoop（以 MCP 形式接入，本次只约定接口与要求，不实现 Hadoop 侧）。
> 本文件是前端（第 6 节）与 Agent（第 5 节）之间的对接依据，也是 Hadoop 侧做 MCP 时必须满足的要求清单。

---

## 1. 范围与总体形态

Agent 是一个常驻服务，位于宿主机，负责：

- 用大模型主控地完成用户请求（可选），或按工具名直接执行（离线直调）；
- 维护对话（可分支、可回溯）、工具表、长任务；
- 把结果以「流式文字 + 结构化数据」两种形态推给前端。

前端与 Agent 之间：**每段对话一条持续推送流**，模型输出、长任务状态、完成后的续写、结构化结果都从这条流推；前端发话、设置上下文、管理对话另发普通请求。

Agent 与 Hadoop 之间：**预留 MCP 边界**。本次不实现 Hadoop 侧，但本文把该边界与要求写清，供后续实现。

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| 对话（conversation） | 上下文单位。有独立历史、独立上下文（当前任务、数据集版本、挂着的长任务）、独立推送流。 |
| 节点（node） | 对话中的一个回合（用户消息或助手回复）。有编号与父节点。 |
| 分支（branch） | 从某个节点另起一条支，旧后文保留。整段对话因此是一棵树。 |
| 推送流（stream） | 每段对话一条服务器推送通道。 |
| 任务（task） | 一次数据治理动作（如发起清洗评分）。有 Agent 侧编号与 Hadoop 侧编号。 |
| 工具（tool） | Agent 可调用的能力。分「本地」与「远程（MCP）」两类。 |
| 模式（mode） | 一次请求的处理方式：`offline`（离线直调）或 `llm`（大模型主控）。 |
| 中断（interrupt） | 长任务完成后发给 Agent 循环层的信号，令模型知晓结果并继续。 |

---

## 3. 前端 ↔ Agent 接口

> **端点命名说明（阶段 6 落地）**：本节的端点名以本文为准；前端整体重写，故此处直接给出
> 具体路径与请求/响应形状。所有「对话相关」端点统一挂在
> `/api/conversations/{对话编号}/…` 之下，与既有的 driver 直取端点（`/api/tasks/…`）互不冲突。

### 3.1 连接与健康状况

| 接口 | 方法 / 路径 | 用途 |
|---|---|---|
| 健康检查 | `GET /health` | 返回服务是否在线、可用工具来源、模型层是否可用等。前端据此显示「已连接 / 未连接」。 |

响应：

```json
{
  "ok": true,
  "service": "agent-http-api/2.0",
  "scope_default": "full",
  "tools": {"count": 11, "sources": ["local"], "available": true},
  "llm": {"supported": false, "configured": false}
}
```

`tools.sources` 取自工具表「来源」字段的并集（当前只会是 `local`；接入 MCP 后出现 `mcp`）；
`tools.available` 表示工具表是否可构建。`llm.supported` 是后端总开关，`llm.configured`
表示当前是否真配齐（enabled + api_base + api_key + model）。

### 3.2 对话管理

| 接口 | 方法 / 路径 | 用途 | 说明 |
|---|---|---|---|
| 新建对话 | `POST /api/conversations` | 创建一段对话 | 请求体可选 `{"title": "…"}`；返回对话编号；随后可开它的推送流。 |
| 列出对话 | `GET /api/conversations` | 列出已有对话 | 含编号、标题/摘要、更新时间。 |
| 打开推送流 | `GET /api/conversations/{对话编号}/stream` | 为某段对话建立推送通道（SSE，`text/event-stream`） | 全程保持；断开表示前端离开该对话。 |

新建 / 列出返回的对话对象统一为：

```json
{"conversation_id": "c0001", "title": "…", "created_at": 1729000000.0,
 "updated_at": 1729000010.0, "current_node_id": 12}
```

新建返回 `{"ok": true, "conversation": { …对话对象… }}`；
列出返回 `{"ok": true, "conversations": [ …对话对象… ]}`（按更新时间倒序）。

### 3.3 发消息

`POST /api/conversations/{对话编号}/messages`

| 字段 | 必填 | 说明 |
|---|---|---|
| 模式 | 是 | `offline` 或 `llm`。请求体键名为 `mode`。 |
| 文本 | 否 | 用户原话。`llm` 模式必填（键名 `text`）。 |
| 工具名 | 否 | `offline` 模式必填（键名 `tool`）；必须是当前工具表里的名字。 |
| 参数 | 否 | `offline` 模式可带（键名 `params`）；未填的走默认值。 |
| 上下文覆盖 | 否 | 本次要指定的当前任务编号 / 数据集版本（键名 `context`：`{"task_id": …, "data_version": …}`），会写入该对话上下文（同 §3.4）。 |
| 模型配置 | 否 | 键名 `llm`：`{enabled, api_base, api_key, model, mode}`；只在本次请求内生效，不落盘。不给则用后端配置（没配则结构化报错）。 |

`offline` 模式下，Agent **不做理解**：校验工具名与参数（补默认值、验合法性），直接调用并返回结果。
`llm` 模式下，由大模型决定调用哪些工具，可多步，流式返回。

**两种模式返回同一形状的回合信封**（HTTP 状态码一律 200，判据是 `ok`）：

```json
{
  "ok": true,
  "engine": "offline",              // "offline" 或 "llm"
  "mode": "offline",                // offline 模式才有；llm 模式的此键省略
  "conversation_id": "c0001",
  "user_node_id": 13, "node_id": 14,
  "final": "…", "reply": "…",
  "steps": [ … ], "task_id": "2026-…", "task_started": true,
  "error": null,
  "offline": {"tool": "get_samples", "arguments": {…}, "defaults_filled": {…}},
  "llm": {"used": false, "steps": [ … ], "note": "…"}
}
```

失败时 `ok=false` 且 `error` 为 §3.10 的错误信封（`CONVERSATION_NOT_FOUND` / `USAGE` /
`UNKNOWN_TOOL` / `POLICY_REJECTED` / `NOT_CONFIGURED` 等）。缺 `mode` / `llm` 模式缺 `text` /
`offline` 模式缺 `tool` 一律 `USAGE`。

### 3.4 上下文设置

`POST /api/conversations/{对话编号}/context`

| 字段 | 说明 |
|---|---|
| 当前任务编号 | 这段对话默认指向哪个任务（键名 `task_id`）。 |
| 数据集版本 | 这段对话默认针对哪个数据版本（键名 `data_version`）。**只作上下文记忆并随调用传递，Agent 不自造版本**（权威在后端）。 |

只覆盖传入的键；传 `null` 表示清空该项。返回：

```json
{"ok": true, "conversation_id": "c0001",
 "context": {"task_id": null, "data_version": "v1", "opts": {…}}}
```

### 3.5 对话树

> **阶段 7（`ag-tree`）已落地。** 对话是**有向树**（一个节点可有多个子节点）：
> 「从某节点继续」= **git 式**在其下新开一支，**旧后文保留为另一条支、不丢弃**。
> 血缘以 Agent 自建表为权威；检查点只作 LLM 上下文消息的回溯载体（见 §4）。
> 端点挂在 `/api/conversations/{对话编号}/…` 之下，与 §3.2–§3.4 同族。

| 接口 | 方法 / 路径 | 用途 |
|---|---|---|
| 读树 | `GET /api/conversations/{对话编号}/tree` | 返回整棵对话树（全部节点 + 当前所在节点/分支） |
| 从节点继续 | `POST /api/conversations/{对话编号}/branches` | 从某节点另起一条支并设为当前所在 |
| 读取单节点 | `GET /api/conversations/{对话编号}/nodes/{节点编号}` | 取某节点完整内容（供前端展开查看） |

**读树**返回：

```json
{"ok": true, "conversation_id": "c0001",
 "nodes": [{"node_id": 1, "parent_id": null, "role": "user",
            "created_at": 1729000000.0, "summary": "…", "seq": 1}],
 "current_node_id": 14,
 "current_branch": "c0001",
 "branches": [{"branch_id": 1, "from_node_id": 2, "thread_id": "c0001:b1",
               "parent_thread": "c0001", "created_at": 1729000010.0}]}
```

`nodes` 是**全部**节点（前端据 `parent_id` 重建树）；每项为编号（`node_id`）、
父节点（`parent_id`，根为 `null`）、角色（`role`）、时间（`created_at`）、
可行摘要（`summary`）与序号（`seq`）。`current_node_id` 是当前所在节点；
`current_branch` 是当前所在分支的线程标识（**主支即对话编号本身**）；`branches`
是已登记的分支。

**从节点继续**请求体：`{"from_node_id": 2}`（缺省则从当前所在节点另起一支）。返回：

```json
{"ok": true, "conversation_id": "c0001",
 "branch": {"branch_id": 1, "from_node_id": 2, "thread_id": "c0001:b1",
            "parent_thread": "c0001", "created_at": 1729000010.0},
 "from_node_id": 2, "current_node_id": 2, "conversation": { …对话对象见 §3.2… }}
```

此后该对话的 `POST …/messages`（§3.3）一律落在新支上：消息只进新支，**主支历史
与旧后文不变**。节点不存在或不属于该对话 → `NODE_NOT_FOUND`。

**读单节点**返回 `{"ok": true, "conversation_id": "c0001", "node": { …节点… }}`；
节点对象在树上字段之外再含 `conversation_id` / `content` / `content_ref` /
`thread_id` / `checkpoint_id`。节点不存在或不属于该对话 → `NODE_NOT_FOUND`。

### 3.6 推送流上的事件类型

推送流只对「这段对话」负责。**线上格式是 Server-Sent Events（`text/event-stream`）**，
每个事件一帧：

```
event: <事件名>
data: {"event":"<事件名>","conversation_id":"c0001","seq":7,"ts":1729000000.0,"data":{…}}

```

`data` 行是该事件的完整 JSON 信封：`event` 事件名、`conversation_id` 所属对话、
`seq` 对话内单调递增序号（只追加不覆盖）、`ts` 产生时刻（`time.time()`）、
`data` 事件各自的字段。服务端在空闲期发注释心跳 `: keep-alive\n\n` 保活；
客户端断开连接时服务端调 `sub.close()` 释放订阅。

事件类型如下（加性扩展）：

| 事件 | 何时发 | 内容要点 |
|---|---|---|
| `tools_snapshot` | 流建立时，及工具表变化时 | 完整工具表（见 3.7），放在 `data.tools`。 |
| `message_delta` | 模型流式输出期间 | 增量文本片段（`data.text`），含所属节点编号（`data.node_id`）。 |
| `message_done` | 一条助手回复完成 | 完整文本 + 节点编号（`data.text` / `data.node_id`）。 |
| `task_status` | 长任务运行期间 | 任务进展（见 3.8），放在 `data`。 |
| `task_resumed` | 长任务完成、模型据此续写时 | 触发续写的任务编号与结果摘要。 |
| `result_payload` | 有结构化结果时 | 供结果板块渲染的数据（见 3.9），放在 `data`。 |
| `node_added` | 新增一个对话节点时 | 节点编号、父节点、角色、时间。 |
| `error` | 出错时 | 错误信封（见 3.10），放在 `data.error`。 |

### 3.7 工具表（由 Agent 主动推送）

Agent 在流建立时推一次完整工具表，其后每当工具集合发生变化（例如 Hadoop 侧新增了 MCP 工具），再推一次。每条工具的结构：

| 字段 | 说明 |
|---|---|
| 名称 | 工具标识（离线模式据此调用）。 |
| 说明 | 给用户与模型看的一句话用途。 |
| 参数 | 参数清单：名称、类型、是否必填、默认值、取值范围。 |
| 来源 | `local`（本地）或 `mcp`（远程 Hadoop）。 |
| 长耗时 | 布尔；是否会长时间运行。 |
| 预计时长 | 长耗时工具给出预计秒数（供挂起展示）。 |

**只读兜底拉取端点**（前端一般不主动拉，工具表由 Agent 主动推）：`GET /api/tools`，
返回 `{"ok": true, "tools": [ … ]}`，元素结构同上表。

### 3.8 任务进展（双编号）

| 字段 | 说明 |
|---|---|
| Agent 任务编号 | Agent 自己生成的编号，对话内引用用这个。 |
| Hadoop 任务编号 | Hadoop 侧回传的编号，供查询状态/结果等调用使用。 |
| 名称 | 给人看的任务名（由 Agent 依据任务内容生成）。 |
| 概括描述 | 一句话说明这次在做什么。 |
| 状态 | 排队 / 运行 / 成功 / 失败 / 已中止（可枚举）。 |
| 阶段与进度 | 当前阶段名 + 进度百分比。 |
| 预计时长 | 长耗时任务的预计剩余/总时长。 |
| 更新时间 | 最近一次刷新时间。 |

**呈现路径**：运行期由 `task_status` 事件主动推（字段即上表，见 §3.6）；同时提供查询端点
（供刷新页面 / 补拉）：

| 接口 | 方法 / 路径 | 返回 |
|---|---|---|
| 列该对话的长任务 | `GET /api/conversations/{对话编号}/tasks` | `{"ok": true, "conversation_id": "c0001", "tasks": [ …任务视图… ]}` |
| 取单个长任务 | `GET /api/conversations/{对话编号}/tasks/{Agent 任务编号}` | `{"ok": true, "task": { …任务视图… }}`；不存在 → `TASK_NOT_FOUND` |

任务视图的键名（双编号）：`agent_task_id` / `hadoop_task_id` / `name` / `summary` /
`status` / `status_cn` / `stage` / `progress_percent` / `estimated_seconds` / `updated_at`。

### 3.9 结构化结果（供「结果板块」）

结果板块不设优先级、一次给全。`result_payload` 至少覆盖：

| 数据块 | 说明 |
|---|---|
| 五维评分对比 | 清洗前后各维度得分与变化。 |
| 数据量变化 | 输入/输出行数、隔离、去重、修复数量。 |
| 隔离样例 | 代表性被隔离记录及其命中规则与原因。 |
| 评估报告 | 报告的引用或全文。 |
| 任务状态 | 编号、名称、状态、版本与时间边界。 |
| 依据与局限 | 评价方法、适用范围、未解决问题。 |

**呈现路径**：有结构化结果时由 `result_payload` 事件主动推（`data` 即下述载荷）；
同时提供取用端点：

`GET /api/conversations/{对话编号}/tasks/{Agent 任务编号}/result` → 结果载荷：

```json
{"ok": true,
 "task": { …任务视图（见 3.8）… },
 "blocks": {"task_status": {…}, "score_comparison": {…}, "data_volume": {…},
            "quarantine_samples": [ … ], "report": …, "limitations": [ … ]}}
```

任务尚未产出结果（未到终态）时，返回 `{"ok": false, "error": {"code": "TASK_NOT_FINISHED",
"message": "…"}}` —— **绝不用旧数据或样例顶替**。

### 3.10 错误信封

| 字段 | 说明 |
|---|---|
| 是否成功 | 布尔。 |
| 错误码 | 结构化错误码（见 5.4 的映射）。 |
| 说明 | 给用户看的中文说明。 |
| 细节 | 可选，附加定位信息。 |

失败时**如实转述**，不编造、不用样例顶替。

---

## 4. Agent 与前端对接的约定

- 推送流**只覆盖单段对话**；切换对话即切换流。
- 工具表**由 Agent 主动推**，前端不主动拉（也可保留一个只读拉取端点作兜底）。
- 对话树与分支**由 Agent 维护血缘**；前端只按事件与接口渲染。
- 结果板块的数据来自 `result_payload`，与模型口述相互独立：**模型口述负责讲解，结果板块负责陈列事实**。

---

## 5. Agent ↔ Hadoop MCP 接口（预留）

本次只约定形状与要求，不实现。Hadoop 侧实现时按此为准。

### 5.1 边界与执行后端抽象

Agent 与 Hadoop 之间只通过「执行后端」交互：同一张工具表里，`local` 工具走本机函数，`mcp` 工具走远程调用。两处共用同一套信封与错误码语义，方便在两种模式间切换。

### 5.2 工具映射（与数据处理程序一一对应）

Hadoop 侧现有的数据治理能力应一一暴露为 MCP 工具，参数尽量全带：

| 能力 | 说明 |
|---|---|
| 校验配置 | 校验清洗/评分方案是否合法，不改数据。 |
| 列方案 | 列出已登记的清洗/评分方案。 |
| 发起全流程 | 清洗 + 前后评分 + 发布，异步返回任务编号。 |
| 只清洗 | 仅清洗链，不评分、不发布。 |
| 独立评分 | 对原始数据 / 指定任务产物 / 已发布数据评分。 |
| 查状态 | 任务进度与阶段。 |
| 取结果 | 权威结果信封。 |
| 取样例 | 清洗后样本 / 隔离区样本。 |
| 取报告 | 结构化或全文报告。 |
| 列任务 | 历史任务列表。 |

### 5.3 MCP 灵活性要求（Hadoop 侧实现时必须满足）

核心原则：**能力上与数据处理程序一一对应、只加不减、错误不丢语义、长任务可被唤醒、可发现、可取证。**

**A. 覆盖与粒度**
- 与现有能力一一对应，参数尽量全暴露。
- 增补**取证类工具**：作业计数、作业日志、产物清单（已发布版本、清洗目录）、任务元数据与版本、时间边界。
- 提供**能力自描述**工具（有哪些工具、各自参数、契约版本），供 Agent 主动推送工具表。

**B. 参数**
- 暴露全部有意义的参数（数据范围、执行方式、任务类型、数据版本、标签、自定义方案）。
- 自定义方案支持「先校验再执行」，校验结果结构化。
- 提供幂等与强制标志，语义与现状一致。

**C. 返回与可观测**
- 结构化、带版本的**结果信封**：任务编号、状态、阶段、进度、计数、耗时、四类版本、时间边界。
- 长任务返回**预计时长**。
- **错误语义无损**：退出码映射为结构化错误码，保留类别、原因、补救提示。
- 产物的**结构化位置信息**，供结果板块直接渲染。

**D. 长任务与异步**
- 发起即时返回句柄；状态可查。
- 最好提供**完成通知/订阅**，让 Agent 能被唤醒（这是「中断」的桥）；至少状态可查。
- 支持**取消/中止**。

**E. 前置与并发**
- 暴露锁/队列状态：能说明「已有任务在跑、占用者是谁、怎么解除」。
- 依赖缺失返回明确的结构化错误。

**F. 发现与版本**
- 工具表每条含：名称、说明、参数与默认值、来源、是否长耗时、预计时长。
- MCP 契约自带**版本号**，可与 Agent 协商；对外**只做加性变更**。

**G. 只读资源**
- 用 MCP 的「资源」能力暴露只读产物：已发布版本、清洗产物、报告、隔离样例、配置。让 Agent 按需取用，不必为每种产物单开工具。

**H. 传输与可用性**
- 网络形式（宿主机能连到虚拟机）；基本访问控制；可查询服务存活/健康。

**I. 安全约束（明确不做）**
- **不给 Agent 开放「执行任意命令」的口子**；破坏性操作要有确认与护栏。

### 5.4 错误码映射

现有退出码须映射为结构化错误码，不得丢失语义：

| 现有退出码 | 含义 | 映射后的错误码（建议） |
|---|---|---|
| 0 | 成功 | 无错误 |
| 2 | 参数非法 | `USAGE` |
| 3 | 任务失败 | `TASK_FAILED` |
| 4 | 不存在 | `TASK_NOT_FOUND` |
| 5 | 未完成 | `TASK_NOT_FINISHED` |
| 6 | 版本冲突 | `VERSION_CONFLICT` |
| 传输层 | 无输出 / 非法输出 / 不可达 / 超时 | `DRIVER_NO_OUTPUT` / `DRIVER_BAD_OUTPUT` / `DRIVER_UNREACHABLE` / `DRIVER_TIMEOUT` |

新增：前置依赖缺失 → `DEPENDENCY_MISSING`；已有任务在跑 → `TASK_ALREADY_RUNNING`。

> **阶段 8 落地（`ag-mcp`，只留接缝）**：以上映射集中在 `agent/mcp_contract.py`
> （`EXIT_CODE_MAP` / `TRANSPORT_CODE_MAP` / `code_for_exit` / `code_for_transport` /
> `normalize_error`），供执行后端层复用。映射**语义无损**：已是结构化码（含 driver
> 自产的 `CONFIG_INVALID`）一律原样保留，绝不降级成通用码；仅有退出码可依时才按上表
> 映射，并把原码记入 `driver_code` 保留。另新增 Agent 侧传输码 `MCP_UNAVAILABLE`
> （MCP 客户端未接入/不可达，与传输层 `DRIVER_*` 同类）。

### 5.5 长任务与完成通知

- 发起返回任务编号；状态可查。
- 完成后，Agent 侧的编排层据「完成通知」或「查询到终态」**发中断**，把结果注入对话，令模型续写。
- 若 MCP 侧不提供主动通知，则退化为 Agent 定期查询到终态后自行发中断——**中断的发出者始终是 Agent 侧**。

### 5.6 版本协商与兼容

- MCP 契约带 `interface_version`；Agent 据其判断能力可用性。
- 契约**只做加性变更**；字段语义变更必须升版本并同步双方。

### 5.7 预留接缝（阶段 8 `ag-mcp` 落地，Hadoop 侧仍不实现）

本阶段只把边界形状固定下来，不连网、不实现 Hadoop 侧：

- **执行后端抽象**（`agent/runtime/tool_exec.py`）：所有后端实现同一接口
  `execute(name, args) -> (ok, envelope)`。`LocalToolBackend` 为本机直调；
  `McpToolBackend` 为远程占位（注入 `McpClient` 即用，未注入时如实报
  `MCP_UNAVAILABLE`）；`RoutingBackend` 按工具表「来源」字段分派（`mcp`→MCP、
  `local`→本机）。运行时经 `build_tool_backend()` 取后端，**换后端不改上层**。
- **MCP 客户端接口**（`agent/mcp_contract.py` 的 `McpClient`，Hadoop 侧须满足）：
  - `list_tools()` —— 工具发现，返回 §3.7 形态条目（`source` 为 `mcp`）；
  - `call_tool(name, args)` —— 调用，返回统一信封；
  - `get_task_status(task_id)` —— 长任务状态可查；
  - `subscribe_task_done(callback, task_id=None)` / `unsubscribe_task_done(handle)`
    —— 完成通知接缝（§5.5；不提供时 Agent 侧退化为定期查询，中断始终由 Agent 发）；
  - `negotiate_version(peer_version=None)` —— 版本协商；
  - `interface_version` 属性 —— 本实现遵循的契约版本；当前预留值为 `"1.0"`。
- **占位实现** `NullMcpClient`：未接入时 `list_tools()` 为空、其余调用给结构化
  `MCP_UNAVAILABLE`，不发起任何网络调用。


---

## 6. 兼容与演进

- 本文件为初稿；定稿前字段名与端点名均可调整。
- 前端整体重写，故必要时可做**破坏性变更**，但**必须同步本文**。
- Hadoop 侧 MCP（第 5 节）本次只留要求，待 Agent 与前端重构完成后再实施。
