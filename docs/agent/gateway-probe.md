# 网关探测结论（Agent v2 阶段 0 交付）

> 日期：2026-09-30 ｜ 脚本：`agent/gateway_probe.py`
> 目的：执行 `docs/agent/llm-agent-plan.md §2.1` 的三项验证，为阶段 1-5 定协议形态。

## 0. 探测方法与纪律（先说清楚）

- **凭据纪律（2026-09-30 定）**：本机其它服务的 API key（含 icspa 等）**一律禁用，不读取、不使用**。
  真实网关探测只认显式环境变量 `GATEWAY_PROBE_BASE / GATEWAY_PROBE_KEY`，
  或项目既有的 `AGENT_LLM_API_BASE / AGENT_LLM_API_KEY`（与 llm_config 同源）。
- **本次环境无可用网关 key**（opencode-go 的 `OPENCODE_GO_API_KEY` 未配置）→ 离线跑内建
  mock 网关，验证的是**我们自己的解析/会话层对两种协议形态都成立**；
  **真实网关的最终结论待 VM 上配好 key 后补跑**（一条命令，见 §4）。
- mock 网关行为：命中带 tools 的请求 → 回原生 `tool_calls`；会话提问按请求内消息历史回答；
  完全无状态。

## 1. V1 协议探测：原生 tool_calls vs 严格 JSON

**做法**：发一版带 `tools: [{function: add}] + tool_choice=auto` 的 chat/completions，
按响应归三类：`native-tool-calls` / `explicit-unsupported` / `tools-ignored`。

**离线结果（mock）**：`native-tool-calls` —— 响应 `message.tool_calls=[add]`，解析层正确识别。

**结论（写进 llm-agent-plan §8.1 #1）**：
- **循环解析层必须双支持**：`message.tool_calls`（原生）与 `{"action":..., "args":...}` / `{"final":...}`
  （严格 JSON）两种形态都实现解析——差一层解析函数，循环、策略、工具表完全复用。
- **真实网关待补跑**：VM 上有 opencode-go key 时执行
  `GATEWAY_PROBE_BASE=http://127.0.0.1:8901/v1 GATEWAY_PROBE_KEY=<占位> python3 agent/gateway_probe.py --live`，
  若返回 `explicit-unsupported` 或 `tools-ignored`，则 llm_loop 的默认路径切严格 JSON
  （代码结构上两种解析器已齐，只是开关一个默认值）。

## 2. V2 会话验证：多轮延续 + 并发隔离 + 未知会话头

**做法**（三项各一例）：
1. 多轮延续：单请求携带完整历史（user → assistant → user），模型正确延续上下文。
2. 并发隔离：两条会话（幸运数字 7 / 99）各自携带自己的历史交错发送，各答各的数字。
3. 未知会话头：请求带 `x-opencode-session: probe-...`，OpenAI 兼容网关应忽略并正常回答。

**离线结果（mock）**：三项全部通过（A=7 / B=99 / 头被容忍）。

**结论（写进 llm-agent-plan §8.1 #2）**：
- **无状态用法成立**：每轮请求携带完整消息历史即可延续对话，**不需要服务器端会话**；
  并发任务只需各自维护消息列表（agent 侧按 task_id/会话号组织）。
- **opencode-go 的会话头**：该网关要求 `x-opencode-session`，由 `hadoop/tools/opencode_proxy.py`
  统一附加（当前为每请求随机 UUID）。无状态用法下随机 UUID 不影响回答正确性
  （模型只读本次请求的消息）；若日后需要网关侧会话延续，**转接头固定在代理侧**，
  不把网关细节渗进 agent 循环（计划 §2.1 第 2 条不变）。

## 3. V3 插件评估：@deepseek-ai/dsh-llm-deepseek

**结论：不采用。**（证据见脚本 `probe_v3()` 输出）

| 项 | 结论 |
|---|---|
| 名称/来源 | `@deepseek-ai/dsh-llm-deepseek` v0.1.1-rc.2，MIT，deepseek-harness 仓库 `packages/llm/llm-deepseek` |
| 负责哪层 | DeepSeek chat-completions 适配器（harness LLM seam 内）：SSE 流式解析、流式 tool_calls 聚合、Files API；**不是**会话管理器、不做重试编排 |
| 能否安装 | 能（MIT），但 Node.js ESM + 约 12 个 cordis 生态 peer 依赖，与工程「Python 标准库、零第三方依赖、零构建」红线冲突 |
| 能否被 llm_client 复用 | 不能直接复用（TS/ESM vs Python stdlib urllib）；非流式场景用不到其 SSE 解析 |
| 与自产循环是否冲突 | 无冲突；但为采用它引入 Node 侧车，维护成本大于收益 |

**借鉴点**：其流式 `tool_calls` 解析印证「协议原生支持 tools」，与 V1 探测同结论；
会话与重试不在插件层，仍由自产循环负责。

## 4. 一条命令补跑（留给 VM）

```bash
# 在 VM 上用 opencode-go（经本地代理）补真实网关结论：
GATEWAY_PROBE_BASE=http://127.0.0.1:8901/v1 \
GATEWAY_PROBE_KEY=anything \
python3 agent/gateway_probe.py --live --json
```

预期产出三行判定（v1.verdict / v2.*.ok / v3.conclusion），把输出粘贴回本文档 §1~§2 即完成收口。