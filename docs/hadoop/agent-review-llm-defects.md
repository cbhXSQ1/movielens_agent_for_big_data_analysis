# Agent（第 5 节）LLM 可选层 & 意图解析 —— 联调缺陷清单

> 整理人：队长侧（Hadoop/driver，132 李丞渊的代码评审 + 本机真机验证）
> 日期：2026-09-29
> 环境：本机实测 `deepseek-flash`（icspa 网关）与 `deepseek-v4-flash`（opencode Zen/Go 网关，
> 经 `hadoop/tools/opencode_proxy.py`）两个模型都跑过。
>
> agent/ 目录我未改动，以下全部是**供李丞渊同学参考**的修复建议；1~4 是可在答辩前修完的缺陷，
> 5 是答辩口径提醒（非代码缺陷）。

---

## L1 `intent.py`：关键词"为什么"撞词（**建议必改**）

**现象**（前端实测两次）：
> 用户问「解释为什么这么评分」，Agent 返回「识别为：取样本」并给出隔离区样例，
> 而不是解释评分/报告。

**根因**：`agent/intent.py` 的 `KEYWORDS["get_samples"]` 包含一个字面词 `"为什么"`，
而 `PRIORITY` 里 `get_samples` 排第二（先于 `get_report` / `task_result` / `clean_evaluate`）。
"为什么"是几乎所有"解释/追问"类句子的惯用词 —— 被它命中后整句被抢进"取样本"。

**影响**：入口面积大。`get_samples` 会被一切"为什么/解释"文案误触；若调用时带任务上下文
还不报错（照常取 5 条样例），用户以为 Agent 答非所问是因为数据问题。

**建议**（三选一，推荐 1+2 组合）：
1. 从 `get_samples` 关键词里**删除** `"为什么"`；
2. 若想保留"为什么被隔离 → 看样本"的语境，改成**短语规则**（如 `为什么.*隔离`），
   不匹配单字；
3. 「解释 / 为什么 / 说明 / 依据」类词建议归到 `get_report`（或新增 explain 意图）。

**备注（LLM 层并不能兜住这件事）**：`mode=fallback` 下规则命中就不问大模型，
"为什么"撞词依旧发生；`mode=always` 下大模型能给出白名单内的正确意图
（实测同一句先后得到 `{"intent":"get_report"}` 与 `{"intent":"get_samples"}`），
但**输出有随机性**，不能作为可靠兜底 —— 词表本身还是要修。

---

## L2 `/api/llm/test` 硬编码 `max_tokens=8`（**建议必改**）

**现象**：配置完全正确（`/health` 显示 `llm: {configured: true}`）时，
`POST /api/llm/test` 稳定返回：

```json
{"ok": false, "reachable": false,
 "error": {"code": "LLM_UNREACHABLE", "message": "返回的 content 为空"}}
```

（icspa 网关 + `deepseek-flash` 实测必然复现；opencode 网关 + `deepseek-v4-flash`
因为推理段短，8 tokens 偶尔够，不一定复现 —— 但真实模型不可控。）

**根因**：`agent/http_api.py` 里测试调用写死 `max_tokens=8`：
```python
got, err = llm_client.chat(cfg, "你只需回复 OK 两个字。", "ping", max_tokens=8)
```
推理型模型（`reasoning_content`）会把前几个 token 全花在推理上，
8 个 token 被吞完 → `content` 为空 → `finish_reason=length`。
（实测该网关返回 `usage.completion_tokens=8, completion_tokens_details.reasoning_tokens=8`。）

**影响**：前端"大模型设置"面板的测试按钮在推理模型上**永远失败**，
演示时观感像"没配好"，与 `configured: true` 矛盾。

**建议**：`max_tokens` 改用 `cfg.max_tokens`（或至少 1024）。一行。

---

## L3 `parse_llm` 用 `max_tokens=200`，推理模型下偶发空 content（建议改）

**现象**：同一条句子重试三次，结果：
```
try0 content='{"intent":"get_report","params":{}}'
try1 "返回的 content 为空"
try2 content='{"intent":"unknown","params":{}}'
```

**根因**：`agent/llm_parse.py` 的 `parse_llm` 调用：
```python
content, err = llm_client.chat(cfg, INTENT_SYSTEM, "用户原话：\n" + text, max_tokens=200)
```
`INTENT_SYSTEM` 本身约 390 字节，加上用户原话后，推理模型需要
更多推理 token，200 上限下偶发被吞空；另外大模型输出**本身不稳定**
（上面三次给了两个不同合法结果）。`mode=always` 时这类抖动会表现为
"LLM 用了但没生效"（回落规则）。

**影响**：`always` 演示模式下偶发回落；`fallback` 模式无感官影响（unknown 才问）。

**建议**：`max_tokens` 提到 `cfg.max_tokens`（本机配的 4096）或至少 1024。

---

## L4 调用失败时 `last_reason()` 返回 None → 前端显示"未知原因"（建议改）

**现象**（`mode=always` 下实测）：
```json
"llm": {"configured": true, "used": false,
        "note": "大模型不可用或返回不合法，已回落到规则解析：未知原因"}
```

**根因**：`agent/llm_client.py` 的 `chat()` 在多个失败分支返回
`(None, "返回的 content 为空")` 等，但 `_LAST_ERROR` 并不是每个分支都赋值；
`agent/llm_parse.py::last_reason()` 与 `agent.py` 的 note 拼接读到的就是 None，
最终落成"未知原因"。实测：`parse_llm` 触发空 content 后，`llm_parse.last_reason()`
返回 `None`。

**影响**：排查方向丢失 —— 明明是"content 为空"，界面只给"未知原因"，
无法区分"没配好 / 超时 / 空内容 / 解析失败"。

**建议**：`chat()` 的**每一个**失败返回点都先写 `_LAST_ERROR = <原因>`；
`parse_llm` 的 JSON 解析失败分支也补一个 reason（如 "大模型输出不是合法 JSON"）。

---

## L5 答辩口径提醒（非代码缺陷）：LLM 模式下的随机性

- `mode=fallback`（默认）：**行为 100% 确定可复现**（规则听不懂才兜底，
  且兜底结果也过白名单校验）—— 答辩主线用它；
- `mode=always`：同一句"解释为什么这么评分"实测得到过 `get_report` 也得到过
  `get_samples`（都在白名单内、参数合法，护栏没有错误放行任何数字/规则）。
  这是大模型的**固有随机性**，不是 bug。答辩若现场演示 always 模式，
  建议措辞为"演示 LLM 的理解与约束能力"，不要承诺"同样的话永远同一个结果"；
- 数字/隔离数/分数**永远不会出自大模型**（占位符 + 三层护栏），这条是卖点，
  可以重点讲。

---

## 附：联调中发现的环境事实（不影响代码，供知悉）

- opencode Zen/Go 网关（`https://opencode.ai/zen/go/v1`）要求 `x-opencode-session`
  请求头，且 Cloudflare 按 TLS 指纹封禁 python-urllib（403 error 1010）；
  已用 `hadoop/tools/opencode_proxy.py`（本地 OpenAI 兼容代理，curl 转发）解决，
  配置 `config/llm.settings.json`（gitignored）指向 `http://127.0.0.1:8901/v1`，
  详见 runbook §4.6。
---

## L6 `intent.py` 决胜算法：固定优先级压过命中数（**建议必改**）

**现象**（前端实测）：
> 「清洗 MovieLens 1M，评估清洗前后的分数，并说明处理了哪些问题」→
> Agent 识别为「获取任务结果（五维对比 / 数据量 / 隔离）」，还自动套了最近任务
> d016-verify-01，而不是发起一次新任务。

**根因**（`agent/intent.py::parse`）：
```python
for cand in PRIORITY:        # ... task_result(第5) ... clean_evaluate(第9)
    if cand in hits:
        intent = cand         # 命中 1 词的 task_result 先赢
        break                 # 命中 3 词的 clean_evaluate 后输
```
`PRIORITY` 固定顺序**完全无视命中数**：本句 clean_evaluate 命中
`清洗/评估/处理` 3 词（置信度 0.5+0.15×3=0.95），task_result 命中
`分数` 1 词（0.65）—— 仍被低优先级的"分数"抢走。
随后 `_pick_task_id` 的最近任务回退（session 记忆）叠加，产生
「我没说查旧任务，你却给了旧任务结果」的用户体验。

**对照**：`mode=always` 下 LLM 同样判成 task_result（实测）—— 句子确有歧义，
但应用语境（前端示例 chip 文案同款句式=发起）下用户意图明确是发起。

**建议**：
1. 决胜改为「**命中数（置信度）优先，PRIORITY 仅用于同分破平**」
   （一行级；本句立刻判对，规则注释里已承认置信度语义存在）；
2. （可选）`INTENT_SYSTEM` 加一句应用约定：句子提到清洗/评估且未指定
   task_id 时默认 `clean_evaluate`，改善 LLM 侧同歧义。

## L7 元问题（"什么叫/为什么这么说"）超出规则能力（记录，不改）

**现象**（同一轮对话）：「什么叫我没指定任务？」→ unknown + 帮助文本。
**解释**：纯规则解析器没有指代/元问题理解能力，意图白名单也无"解释系统自身"
类别；这是设计边界（离线、确定、可答辩），建议文档如实说明而非堆词表。

---

## L8 前端 20s 超时 × 网关延迟 2.4~33s（**建议必改，当前最影响体验**）

**现象**（前端实测）：「清洗 MovieLens 1M，评估清洗前后的分数…」→
「后端响应超时：请求超过 20 秒未返回 / 任务可能仍在跑」。

**分段计时证据**（2026-09-29 深夜，本机）：

| 段 | 耗时 |
|---|---|
| 直连 opencode 网关（短句，非流式） | **18.9s**（首字节 18.9s：网关生成完整响应才返回） |
| 经本地代理（8901） | 33.0s（代理每请求新起 curl 进程 + 网关并发排队） |
| `/api/llm/test`（agent 链路，短输出） | 6.9s |
| `/api/chat` 完整意图句 | 12.4s（`engine=llm, used=True`） |
| 同句直连 ×8（连续窗口） | 2.4 / 3.4 / 6.9 / 11.8 / 13.7 / 15.4 / 29.7s（+1 次 5xx） |

**结论**：LLM 链路**确实连通**（多次 `engine=llm` 铁证 + 8/8 成功返回）；
延迟主力在 opencode 网关本身（非流式、生成完才回、推理模型），
前端 `TIMEOUT_MS=20000` 必然被慢请求打穿。

**风险窗口**：前端 abort 只断连接、**不取消后端** —— 若慢请求最终判为
发起意图（clean_evaluate），任务会在用户看到"超时"的情况下真实启动。
本次未发生（慢请求恰好判查询/未发起），但窗口真实存在。

**建议**：
1. 前端 `frontend/js/api.js` 第 26 行 `TIMEOUT_MS` 20s → 60s（与 result/report 一致）；
2. （治本）`llm_client` 支持 `stream=true`：首 token 即可返回，延迟感受从
   数十秒降到秒级 —— 同学侧改动，答辩前可选；
3. 代理保留：主要延迟在网关，代理仅 ~几秒级开销。

## L9 网关路由漂移：同一请求跨时刻行为不一致（记录，答辩口径）

**现象**：同一句「清洗…评估分数」+ 同一 INTENT_SYSTEM：
- 22:xx 通过 `/api/chat` 两次得 `task_result`；同时段直连 3 次得
  `get_report` / 空 content / `unknown`；
- 23:xx 连续直连 8 次（deepseek-v4-flash ×4 + qwen3.7-plus ×4）**全部**
  `clean_evaluate`。

**解释**：非"同实例采样随机"（连续窗口内完全稳定），更像 opencode 网关把
请求**路由到不同后端实例**（channel 差异），行为跨时刻漂移。
用户"绝对不是随机性"的判断成立 —— 是路由级漂移，不是温和噪声。

**建议**：答辩措辞改用「LLM 理解正常（连续 8/8 判对），但网关路由可能
导致跨时刻结果不一致；规则层保证确定性兜底」；不要把"随机"当托词。
