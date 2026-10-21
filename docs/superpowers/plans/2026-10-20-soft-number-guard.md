# 软护栏（数字溯源·行内标红）实施计划

> 目标：数字护栏从"拦截 + 强制重写"改为**软提示**；**本轮没有工具调用就完全跳过**；
> 提示方式为**在回答原文里把找不到出处的数字标红**（悬停显示原因），不单独列清单、不修改回答文字。

## Global Constraints
- 不修改模型回答原文（只返回位置区间，前端负责标红）。
- 无工具调用（`steps` 为空）→ 跳过护栏检查。
- 契约加性：结果信封新增可选 `guard_warning`（字符串）与 `guard_spans`（数组），不升版本。
- 文案自然中文，不出现「冒烟/对账/口径」。

## Task A：后端软护栏 + 位置区间

**Files:** `agent/number_guard.py`、`agent/runtime/graph.py`、`agent/runtime/runtime.py`、`agent/tests/test_stage2_number_guard.py`、`agent/tests/test_stage3_graph.py`

1. `number_guard.find_unauthorized(final, authorized)`：
   - 保留现有 `number`/`snippet`，**新增 `start`/`end`**：在原文本中的字符下标。
   - 为了在"保留原文长度"的前提下跳过列表序号：把行首列表标记（`1.` / `2、` / `(3)` / `（4）`）**替换为等长空格**再扫描，这样后续数字的下标不偏移。
   - 数字匹配要能整段吃下千分位（如 `100,830`）并保留下标；比较时仍用规范化 token。
2. `graph.py`：
   - `RuntimeState` 增 `guard_warning: str` 与 `guard_spans: list`。
   - `_guard_final`：`steps` 为空 → 返回 `None`；否则若有未溯源数字 → 返回
     `{"guard_warning": "以下数字未能在工具结果中找到：" + number_guard.describe(records), "guard_spans": [{"number","start","end"}, ...]}`；
     **不再重写、不再 blocked**。
   - `model_node`：有提示时仍返回 `final`/`messages`，并带上 `guard_warning`/`guard_spans`。
3. `runtime.py` `_finish_turn`：成功信封加 `"guard_warning": ... or None` 与 `"guard_spans": [...]`；有提示时 `llm.note` 改为「由大模型编排 N 步工具调用；部分数字未能在工具结果中找到（已在回答中标红）」。
4. 测试：
   - 原"编造即拦截"改为"放行 + `guard_warning` 含 888888 + `guard_spans` 中该数字的区间能截出 `888888`"。
   - 无工具调用时即使回答含数字也**不提示**。
   - 列表序号不产生 span（"1. 2. 3."）。

## Task B：前端行内标红

**Files:** `frontend/src/api/types.ts`、`frontend/src/stores/chat.ts`、`frontend/src/components/chat/MessageList.vue`、`frontend/src/__tests__/*`

1. `RoundEnvelope` 增 `guard_warning?: string | null`、`guard_spans?: Array<{ number: string; start: number; end: number }>`。
2. `ChatMessage` 增 `warning?: string | null`、`warningSpans?: Array<{ number; start; end }>`。
3. `chat.send`：收到 `guard_spans`/`guard_warning` 时挂到对应助手节点（节点未到先暂存，到达时应用；按 `envelope.node_id`）。
4. `MessageList`：把 `msg.text` 按 `warningSpans` 切片渲染，命中的数字用 `<span class="message__num-warn" title="未能在工具结果中找到">` 包裹（**红色/波浪下划线**）；其余文字不变。若只有 `warning` 没有 spans（兜底），在消息下方显示一条琥珀色提示。
5. 测试：发送后消息带 `warningSpans`；组件渲染出标红片段且文字不变；无 spans 时有兜底提示。

## 验收
- 真机：回答含无法溯源数字 → 回答正常返回，数字**红色标出**（悬停有提示）；不再出现 `FABRICATED_NUMBER` / `MODEL_ERROR: 模型未给出有效回答`（除非模型真的给出空内容）。
