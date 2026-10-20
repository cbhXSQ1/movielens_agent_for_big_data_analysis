// FB.4 · 业务端点封装：§3.1 健康、§3.2 对话、§3.3 发消息、§3.4 上下文、
// §3.5 对话树 / 分支 / 单节点、§3.7 工具表兜底、§3.8 任务、§3.9 结果。
// 仅做「路径 + 方法 + 类型」的薄封装，全部经 http.ts 统一解包与错误处理。

import { getJson, postJson, requestJson, type HttpOptions } from './http'
import { conversationStreamUrl } from './sse'
import type {
  AgentMode,
  ArtifactListResponse,
  BranchResponse,
  ContextPatch,
  ContextResponse,
  ConversationListResponse,
  CreateConversationResponse,
  DeleteConversationResponse,
  HealthResponse,
  LlmConfigOverride,
  LlmTestResponse,
  LlmSendRequest,
  MessagesResponse,
  NodeId,
  NodeResponse,
  OfflineSendRequest,
  ReasoningEffort,
  RoundEnvelope,
  SendMessageRequest,
  TaskListResponse,
  TaskResponse,
  ToolsResponse,
  TreeResponse,
  ResultPayload,
} from './types'

function cidPath(conversationId: string, suffix: string): string {
  return `/api/conversations/${encodeURIComponent(conversationId)}${suffix}`
}

// ---------------------------------------------------------------------------
// §3.1 健康检查
// ---------------------------------------------------------------------------

export function health(options: HttpOptions = {}): Promise<HealthResponse> {
  return getJson<HealthResponse>('/health', options)
}

/**
 * §3.3 / §9.4 大模型连通性自检：把当前表单里的 `llm` 配置发到后端，
 * 让它代为探一次真实上游。后端未启用该可选层时返回 `LLM_UNSUPPORTED`。
 * 返回里不含 api_key（后端已脱敏）。
 */
export function testLlm(
  config: LlmConfigOverride,
  options: HttpOptions = {},
): Promise<LlmTestResponse> {
  return postJson<LlmTestResponse>(
    '/api/llm/test',
    { llm: config },
    { ...options, timeoutMs: options.timeoutMs ?? 15_000 },
  )
}

// ---------------------------------------------------------------------------
// §3.2 对话管理
// ---------------------------------------------------------------------------

export function createConversation(
  title?: string,
  options: HttpOptions = {},
): Promise<CreateConversationResponse> {
  return postJson<CreateConversationResponse>(
    '/api/conversations',
    title ? { title } : {},
    options,
  )
}

export function listConversations(
  options: HttpOptions = {},
): Promise<ConversationListResponse> {
  return getJson<ConversationListResponse>('/api/conversations', options)
}

/** §3.2 删除对话：成功 `{ok:true, conversation_id}`；不存在为 `CONVERSATION_NOT_FOUND`。 */
export function deleteConversation(
  conversationId: string,
  options: HttpOptions = {},
): Promise<DeleteConversationResponse> {
  return requestJson<DeleteConversationResponse>(
    cidPath(conversationId, ''),
    { ...options, method: 'DELETE' },
  )
}

/** §3.2 打开推送流地址（供 sse.ts / 视图用）。 */
export const streamUrl = conversationStreamUrl

// ---------------------------------------------------------------------------
// §3.3 发消息
// ---------------------------------------------------------------------------

export interface SendOptions {
  context?: ContextPatch
  llm?: LlmConfigOverride
  timeoutMs?: number
  signal?: AbortSignal
}

/** 通用发消息（offline / llm 同一形状信封）。
 *
 * 聊天回合会走多步工具调用 + 推理，后端上限已放宽到 5 分钟；请求超时同步放宽，
 * 否则前端会先于后端断开（表现成"超时"而实际还在跑）。进度由 SSE 实时推送。
 */
export function sendMessage(
  conversationId: string,
  request: SendMessageRequest,
  options: SendOptions = {},
): Promise<RoundEnvelope> {
  return postJson<RoundEnvelope>(
    cidPath(conversationId, '/messages'),
    {
      ...request,
      ...(options.context ? { context: options.context } : {}),
      ...(options.llm ? { llm: options.llm } : {}),
    },
    { timeoutMs: options.timeoutMs ?? 300_000, signal: options.signal },
  )
}

/** §3.3 offline 直调（按工具名 + 可选参数）。 */
export function sendOffline(
  conversationId: string,
  tool: string,
  params?: Record<string, unknown>,
  options: SendOptions = {},
): Promise<RoundEnvelope> {
  const request: OfflineSendRequest = {
    mode: 'offline',
    tool,
    ...(params ? { params } : {}),
  }
  return sendMessage(conversationId, request, options)
}

/** §3.3 llm 主控（必填 text）。`reasoningEffort` 透传为请求体顶层 `reasoning_effort`。 */
export function sendLlm(
  conversationId: string,
  text: string,
  options: SendOptions = {},
  reasoningEffort?: ReasoningEffort,
): Promise<RoundEnvelope> {
  const request: LlmSendRequest = {
    mode: 'llm',
    text,
    ...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
  }
  return sendMessage(conversationId, request, options)
}

/** 发一条消息并指定模式（视图层模式切换用）。 */
export function sendByMode(
  conversationId: string,
  mode: AgentMode,
  payload: {
    text?: string
    tool?: string
    params?: Record<string, unknown>
    reasoning_effort?: ReasoningEffort
  },
  options: SendOptions = {},
): Promise<RoundEnvelope> {
  if (mode === 'offline') {
    if (!payload.tool) {
      return Promise.reject(
        new Error('offline 模式必须提供工具名 tool'),
      )
    }
    return sendOffline(conversationId, payload.tool, payload.params, options)
  }
  if (!payload.text) {
    return Promise.reject(new Error('llm 模式必须提供文本 text'))
  }
  return sendLlm(conversationId, payload.text, options, payload.reasoning_effort)
}

// ---------------------------------------------------------------------------
// §3.4 上下文设置
// ---------------------------------------------------------------------------

export function setContext(
  conversationId: string,
  patch: ContextPatch,
  options: HttpOptions = {},
): Promise<ContextResponse> {
  return postJson<ContextResponse>(
    cidPath(conversationId, '/context'),
    patch,
    options,
  )
}

// ---------------------------------------------------------------------------
// §3.5 对话树 / 分支 / 单节点
// ---------------------------------------------------------------------------

export function readTree(
  conversationId: string,
  options: HttpOptions = {},
): Promise<TreeResponse> {
  return getJson<TreeResponse>(cidPath(conversationId, '/tree'), options)
}

export function readNode(
  conversationId: string,
  nodeId: NodeId,
  options: HttpOptions = {},
): Promise<NodeResponse> {
  return getJson<NodeResponse>(
    cidPath(conversationId, `/nodes/${encodeURIComponent(String(nodeId))}`),
    options,
  )
}

/** 从某节点另起一支（缺省从当前所在节点）。 */
export function branchFrom(
  conversationId: string,
  fromNodeId?: NodeId,
  options: HttpOptions = {},
): Promise<BranchResponse> {
  return postJson<BranchResponse>(
    cidPath(conversationId, '/branches'),
    fromNodeId === undefined ? {} : { from_node_id: fromNodeId },
    options,
  )
}

// ---------------------------------------------------------------------------
// §3.7 工具表（只读兜底；正常由 tools_snapshot 事件主动推）
// ---------------------------------------------------------------------------

export function fetchTools(options: HttpOptions = {}): Promise<ToolsResponse> {
  return getJson<ToolsResponse>('/api/tools', options)
}

// ---------------------------------------------------------------------------
// §3.8 任务进展（双编号）
// ---------------------------------------------------------------------------

export function listTasks(
  conversationId: string,
  options: HttpOptions = {},
): Promise<TaskListResponse> {
  return getJson<TaskListResponse>(cidPath(conversationId, '/tasks'), options)
}

export function getTask(
  conversationId: string,
  agentTaskId: string,
  options: HttpOptions = {},
): Promise<TaskResponse> {
  return getJson<TaskResponse>(
    cidPath(
      conversationId,
      `/tasks/${encodeURIComponent(agentTaskId)}`,
    ),
    options,
  )
}

// ---------------------------------------------------------------------------
// §3.12 产物（恢复端点）
// ---------------------------------------------------------------------------

/** 恢复某对话的产物列表（§3.12；刷新 / 切换对话补拉）。 */
export function listArtifacts(
  conversationId: string,
  options: HttpOptions = {},
): Promise<ArtifactListResponse> {
  return getJson<ArtifactListResponse>(
    cidPath(conversationId, '/artifacts'),
    options,
  )
}

// ---------------------------------------------------------------------------
// §3.13 消息历史（刷新 / 切换后恢复对话内容）
// ---------------------------------------------------------------------------

export function listMessages(
  conversationId: string,
  options: HttpOptions = {},
): Promise<MessagesResponse> {
  return getJson<MessagesResponse>(cidPath(conversationId, '/messages'), options)
}

// ---------------------------------------------------------------------------
// §3.9 结构化结果
// ---------------------------------------------------------------------------

/**
 * 取任务结构化结果。注意：任务失败时 `ok=false` 但仍带 `blocks`（§3.9），
 * 未到终态时才抛 `TASK_NOT_FINISHED`。调用方据返回的 `ok` 判读。
 */
export function getTaskResult(
  conversationId: string,
  agentTaskId: string,
  options: HttpOptions = {},
): Promise<ResultPayload> {
  return requestJson<ResultPayload>(
    cidPath(
      conversationId,
      `/tasks/${encodeURIComponent(agentTaskId)}/result`,
    ),
    { ...options, method: 'GET' },
  )
}
