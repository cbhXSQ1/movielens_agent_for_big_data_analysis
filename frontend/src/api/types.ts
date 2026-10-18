// 数据层类型：严格对齐《Agent_接口文档.md》§3（§3.1–§3.11）。
// 字段名/形状以接口文档为准；这里是前端与 Agent 契约的唯一 TS 出处。

// ---------------------------------------------------------------------------
// §3.10 错误信封与统一外壳
// ---------------------------------------------------------------------------

export interface ErrorEnvelope {
  code: string
  message: string
  // 附加定位信息（可选）：driver_code / path / node_id 等
  [key: string]: unknown
}

/** 所有 JSON 响应的公共外壳：`ok` 是唯一判据，`error` 仅在失败时出现。 */
export interface BaseEnvelope {
  ok: boolean
  error?: ErrorEnvelope | null
}

// ---------------------------------------------------------------------------
// §3.2 对话
// ---------------------------------------------------------------------------

export type ConversationId = string
export type NodeId = number
export type AgentTaskId = string

export interface Conversation {
  conversation_id: ConversationId
  title: string
  created_at: number
  updated_at: number
  current_node_id: NodeId | null
}

export interface ConversationListResponse extends BaseEnvelope {
  conversations: Conversation[]
}

export interface CreateConversationResponse extends BaseEnvelope {
  conversation: Conversation
}

/** §3.2 删除对话响应：成功 `{ok:true, conversation_id}`，不存在则 `CONVERSATION_NOT_FOUND`。 */
export interface DeleteConversationResponse extends BaseEnvelope {
  conversation_id: ConversationId
}

// ---------------------------------------------------------------------------
// §3.5 对话树 / 节点 / 分支
// ---------------------------------------------------------------------------

export type NodeRole = 'user' | 'assistant' | string

/** 读树时的节点（§3.5）：编号、父节点、角色、时间、可行摘要、序号。 */
export interface TreeNode {
  node_id: NodeId
  parent_id: NodeId | null
  role: NodeRole
  created_at: number
  summary: string
  seq: number
}

/** 读单节点时的完整节点（§3.5，full=true）。 */
export interface NodeDetail extends TreeNode {
  conversation_id: ConversationId
  content: string
  content_ref: string | null
  thread_id: string
  checkpoint_id: string | null
}

export interface Branch {
  branch_id: number
  from_node_id: NodeId
  thread_id: string
  parent_thread: string
  created_at: number
}

export interface ConversationTree {
  conversation_id: ConversationId
  nodes: TreeNode[]
  current_node_id: NodeId | null
  current_branch: string
  branches: Branch[]
}

export interface TreeResponse extends BaseEnvelope, ConversationTree {}

export interface NodeResponse extends BaseEnvelope {
  conversation_id: ConversationId
  node: NodeDetail
}

export interface BranchResponse extends BaseEnvelope {
  conversation_id: ConversationId
  branch: Branch
  from_node_id: NodeId
  current_node_id: NodeId | null
  conversation: Conversation
}

// ---------------------------------------------------------------------------
// §3.3 发消息 / §3.4 上下文
// ---------------------------------------------------------------------------

export type AgentMode = 'offline' | 'llm'

/** §3.3 思考档位：仅 `llm` 模式透传为模型请求体顶层 `reasoning_effort`。 */
export type ReasoningEffort = 'low' | 'medium' | 'high'

export interface LlmConfigOverride {
  enabled?: boolean
  api_base?: string
  api_key?: string
  model?: string
  mode?: string
}

/** §3.4 上下文：只覆盖传入的键；`null` 表示清空。 */
export interface ContextPatch {
  task_id?: string | null
  data_version?: string | null
}

export interface ConversationContext {
  task_id?: string | null
  data_version?: string | null
  opts?: Record<string, unknown>
  [key: string]: unknown
}

export interface OfflineSendRequest {
  mode: 'offline'
  tool: string
  params?: Record<string, unknown>
  context?: ContextPatch
  llm?: LlmConfigOverride
}

export interface LlmSendRequest {
  mode: 'llm'
  text: string
  context?: ContextPatch
  llm?: LlmConfigOverride
  /** 思考档位（§3.3）；缺省不发该字段，行为与既有完全一致。 */
  reasoning_effort?: ReasoningEffort
}

export type SendMessageRequest = OfflineSendRequest | LlmSendRequest

export interface OfflineInfo {
  tool: string
  arguments: Record<string, unknown>
  defaults_filled: Record<string, unknown>
}

export interface LlmInfo {
  used: boolean
  steps: unknown[]
  note?: string
}

/** §3.3 回合信封：offline / llm 两种模式同形。 */
export interface RoundEnvelope extends BaseEnvelope {
  engine: AgentMode
  mode?: AgentMode
  conversation_id: ConversationId
  user_node_id?: NodeId
  node_id?: NodeId
  final?: string | null
  reply?: string
  steps?: unknown[]
  task_id?: string | null
  task_started?: boolean
  /** §3.12 加性字段：到本回合为止该对话可用的产物引用完整列表。 */
  artifacts?: ArtifactRef[]
  offline?: OfflineInfo
  llm?: LlmInfo
}

export interface ContextResponse extends BaseEnvelope {
  conversation_id: ConversationId
  context: ConversationContext
}

// ---------------------------------------------------------------------------
// §3.7 工具表
// ---------------------------------------------------------------------------

export type ToolSource = 'local' | 'mcp' | string

export interface ToolParamEntry {
  name: string
  type: string
  required: boolean
  default?: unknown
  enum?: unknown[]
  minimum?: number
  maximum?: number
  description?: string
}

export interface ToolSpec {
  name: string
  description: string
  parameters: ToolParamEntry[]
  source: ToolSource
  long_running: boolean
  estimated_seconds: number | null
}

export interface ToolsResponse extends BaseEnvelope {
  tools: ToolSpec[]
}

// ---------------------------------------------------------------------------
// §3.8 任务（双编号）
// ---------------------------------------------------------------------------

export type TaskStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'aborted'
  | string

export interface TaskView {
  agent_task_id: AgentTaskId
  hadoop_task_id: string | null
  name: string
  summary: string
  status: TaskStatus
  status_cn: string
  stage: string
  progress_percent: number
  estimated_seconds: number | null
  updated_at: number
}

export interface TaskListResponse extends BaseEnvelope {
  conversation_id: ConversationId
  tasks: TaskView[]
}

export interface TaskResponse extends BaseEnvelope {
  task: TaskView
}

// ---------------------------------------------------------------------------
// §3.9 结构化结果
// ---------------------------------------------------------------------------

export interface ResultBlocks {
  task_status?: TaskView
  score_comparison?: unknown
  data_volume?: unknown
  quarantine_samples?: unknown
  report?: unknown
  limitations?: unknown
  [key: string]: unknown
}

/**
 * 结果载荷。注意：任务失败时会带 `blocks` 但 `ok=false` **且不带 `error`**
 * （§3.9），故 http 层不以 `ok=false` 抛错，需调用方自行判读。
 */
export interface ResultPayload extends BaseEnvelope {
  task: TaskView
  blocks: ResultBlocks
}

// ---------------------------------------------------------------------------
// §3.12 产物（回合产物引用）
// ---------------------------------------------------------------------------

/** 产物来源：任务产物文件 / 方案文件。 */
export type ArtifactSource = 'task_file' | 'scheme' | string

/** 产物种类：按扩展名粗分；`scheme` 为方案文件。 */
export type ArtifactKind =
  | 'json'
  | 'md'
  | 'dat'
  | 'text'
  | 'other'
  | 'scheme'
  | string

/**
 * 产物类别（§3.12）：远端给出的六类 + 「其它」兜底。
 * `category` 字段本身仍按字符串宽松接收，未知取值按「其它」呈现。
 */
export type ArtifactCategory =
  | '数据'
  | '隔离'
  | '报告'
  | '指标'
  | '元数据'
  | '日志'
  | '其它'

/** 一条产物引用（§3.12）：`id` 稳定、可去重，一条一个文件。 */
export interface ArtifactRef {
  id: string
  /** 友好标题；不认识则回落成文件路径。 */
  title: string
  /** 报告 | 评分 | 证据 | 文件 | 依据 | 其它 */
  category: string
  kind: ArtifactKind
  source: ArtifactSource
  /** **Hadoop 任务编号**（不是 Agent 任务号）；下载 / 预览一律按此定位远端任务目录。 */
  task_id: string | null
  /** 相对任务目录路径；scheme 为 null。 */
  path: string | null
  /** 方案文件名；task_file 为 null。 */
  scheme_name: string | null
}

export interface ArtifactListResponse extends BaseEnvelope {
  conversation_id: ConversationId
  artifacts: ArtifactRef[]
}

// ---------------------------------------------------------------------------
// §3.13 消息历史（刷新 / 切换后恢复对话内容）
// ---------------------------------------------------------------------------

/** 一条历史节点（来自节点表）：`content` 为完整内容，按 `seq` 升序。 */
export interface MessageHistoryItem {
  node_id: NodeId
  parent_id: NodeId | null
  role: NodeRole
  content: string
  created_at: number
  seq: number
}

export interface MessagesResponse extends BaseEnvelope {
  conversation_id: ConversationId
  messages: MessageHistoryItem[]
}

// ---------------------------------------------------------------------------
// §3.6 推送流事件（九类 + 信封外壳）
// ---------------------------------------------------------------------------

export const STREAM_EVENT_NAMES = [
  'tools_snapshot',
  'message_delta',
  'message_done',
  'task_status',
  'task_resumed',
  'result_payload',
  'node_added',
  'artifact_added',
  'error',
] as const

export type StreamEventName = (typeof STREAM_EVENT_NAMES)[number]

export interface ToolsSnapshotData {
  tools: ToolSpec[]
}

export interface MessageDeltaData {
  text: string
  node_id: NodeId
}

export interface MessageDoneData {
  text: string
  node_id: NodeId
}

/** `task_status` 的 data 即 §3.8 的任务视图。 */
export type TaskStatusData = TaskView

export interface TaskResumedData {
  agent_task_id: AgentTaskId
  hadoop_task_id: string | null
  status: TaskStatus
  result_summary: string
}

/** `result_payload` 的 data 即 §3.9 结果载荷。 */
export type ResultPayloadData = ResultPayload

export interface NodeAddedData {
  node_id: NodeId
  parent_id: NodeId | null
  role: NodeRole
  created_at: number
}

/** `artifact_added`：本回合新增的一条产物引用（§3.12）。 */
export interface ArtifactAddedData {
  artifact: ArtifactRef
}

export interface ErrorData {
  error: ErrorEnvelope
  node_id?: NodeId
}

export interface StreamEventDataMap {
  tools_snapshot: ToolsSnapshotData
  message_delta: MessageDeltaData
  message_done: MessageDoneData
  task_status: TaskStatusData
  task_resumed: TaskResumedData
  result_payload: ResultPayloadData
  node_added: NodeAddedData
  artifact_added: ArtifactAddedData
  error: ErrorData
}

export interface TypedStreamEnvelope<K extends StreamEventName> {
  event: K
  conversation_id: ConversationId
  seq: number
  ts: number
  data: StreamEventDataMap[K]
}

export type AnyStreamEnvelope = {
  [K in StreamEventName]: TypedStreamEnvelope<K>
}[StreamEventName]

export function isStreamEventName(value: unknown): value is StreamEventName {
  return (
    typeof value === 'string' &&
    (STREAM_EVENT_NAMES as readonly string[]).includes(value)
  )
}

// ---------------------------------------------------------------------------
// §3.1 健康检查
// ---------------------------------------------------------------------------

export interface HealthTools {
  count: number
  sources: ToolSource[]
  available: boolean
}

export interface HealthLlm {
  supported: boolean
  configured: boolean
}

export interface HealthResponse extends BaseEnvelope {
  service: string
  scope_default: string
  tools: HealthTools
  llm: HealthLlm
}

// ---------------------------------------------------------------------------
// §3.3 / §9.4 大模型连通性自检（POST /api/llm/test）
// ---------------------------------------------------------------------------

/** 自检返回：`reachable` 是否连得上、`latency_ms` 往返耗时、`config` 回显（已脱敏，不含 api_key）。 */
export interface LlmTestResponse extends BaseEnvelope {
  reachable?: boolean
  latency_ms?: number
  config?: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// §3.11 资源与文件
// ---------------------------------------------------------------------------

// §3.11.1 原始数据
export interface RawFile {
  name: string
  size: number
  updated_at: number
}

export interface RawDataset {
  name: string
  path: string
  files: RawFile[]
  size: number
  updated_at: number
}

export interface RawListResponse extends BaseEnvelope {
  dir: string
  datasets: RawDataset[]
}

// §3.11.2 清洗后数据（已发布）
export interface PublishedTable {
  name: string
  file: string
  size: number
}

export interface PublishedDataset {
  data_version: string
  source_task_id: string
  path: string
  tables: PublishedTable[]
  published_at: number
}

export interface PublishedListResponse extends BaseEnvelope {
  datasets: PublishedDataset[]
}

// §3.11.3 方案文件
export interface SchemeInfo {
  name?: string
  path?: string
  [key: string]: unknown
}

/** driver `/api/schemes` 的元信息列表信封（形状以 driver 为准，宽松收）。 */
export interface SchemeListResponse extends BaseEnvelope {
  [key: string]: unknown
}

export interface SchemeContentResponse extends BaseEnvelope {
  name: string
  path: string
  content: unknown
}

export interface ValidationResult {
  ok?: boolean
  [key: string]: unknown
}

/** 上传成功后远端回执的方案元信息（形状以能力服务为准，宽松收）。 */
export interface SchemeUploadScheme {
  scheme_id?: string
  type?: string
  version?: string
  status?: string
  path?: string
  description?: string
  [key: string]: unknown
}

/**
 * 方案上传回执：`saved`/`validation` 保持兼容；
 * `scheme`/`overwritten`/`size_bytes` 为经 Agent 转发到远端能力服务后新增。
 */
export interface SchemeUploadResponse extends BaseEnvelope {
  saved: { name: string; path: string }
  validation?: ValidationResult
  scheme?: SchemeUploadScheme
  overwritten?: boolean
  size_bytes?: number
}

// §3.11.4 产出文件
export type FileKind = 'json' | 'md' | 'dat' | 'text' | 'other' | string

export interface TaskFile {
  path: string
  size: number
  kind: FileKind
}

export interface TaskFilesResponse extends BaseEnvelope {
  task_id: string
  files: TaskFile[]
}

export interface FilePreviewResponse extends BaseEnvelope {
  path: string
  previewable?: boolean
  kind?: FileKind
  size?: number
  content?: string
  truncated?: boolean
}
