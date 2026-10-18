// 阶段 C · 外壳的共享常量与类型。
// 左栏四个面板 key 在此单点定义；中栏结果标签改为**动态**（默认只有「对话」，
// 其余标签由 artifacts store 按对话的产物决定，每个产物一个标签）。

/** 左栏面板标识（①对话列表 ②对话树 ③资源 ④任务进度）。 */
export type PanelKey = 'list' | 'tree' | 'resources' | 'progress'

/**
 * 中栏结果标签标识。基础标签为 `'chat'`；其余为产物 id（`ArtifactRef.id`），
 * 由 artifacts store 决定，故这里是开放的字符串。
 */
export type ResultTabKey = string

export interface ShellPanelDef {
  key: PanelKey
  label: string
}

export interface ShellTabDef {
  key: ResultTabKey
  label: string
  /** 是否显示关闭按钮（产物标签可关；基础「对话」不可关）。 */
  closable?: boolean
}

/** 左栏图标条顺序与文案（图标名与 key 同名，见 ShellIcon）。 */
export const LEFT_PANELS: readonly ShellPanelDef[] = [
  { key: 'list', label: '对话列表' },
  { key: 'tree', label: '对话树' },
  { key: 'resources', label: '资源' },
  { key: 'progress', label: '任务进度' },
]

/** 左栏面板可拖拽宽度（宽屏内联时）的边界与默认值（px）。 */
export const LEFT_RAIL_WIDTH = {
  /** 最窄（再窄会挤掉标签行文字）。 */
  min: 240,
  /** 最宽（再宽会过分压缩中栏）。 */
  max: 560,
  /** 默认宽度（与历史固定宽度一致）。 */
  default: 320,
  /** 键盘方向键每次调整的步长。 */
  step: 16,
} as const

/** 中栏基础标签：只有「对话」；默认选中它。 */
export const CHAT_TAB: ShellTabDef = { key: 'chat', label: '对话' }

/** 中栏标签前缀（基础标签，不含动态产物标签）。 */
export const BASE_TABS: readonly ShellTabDef[] = [CHAT_TAB]

/** 壳层状态持久化键（localStorage）。 */
export const SHELL_STORAGE_KEYS = {
  /** 左栏展开/收起：'1' 收起、'0' 展开。 */
  collapsed: 'movielens.shell.collapsed',
  /** 左栏当前面板（list/tree/resources/progress）。 */
  panel: 'movielens.shell.panel',
  /** 左栏内联面板宽度（px，宽屏可拖拽调宽）。 */
  leftWidth: 'movielens.shell.leftWidth',
  /** 中栏当前标签（'chat' 或产物 id）。 */
  tab: 'movielens.shell.tab',
  /** 当前对话编号（刷新后回到同一对话）。 */
  conversation: 'movielens.currentConversation',
  /** 每个对话「已打开的产物标签」集合（{对话编号: [产物id]}）。 */
  openTabs: 'movielens.openTabs',
  /** 中栏「资源标签」（模型 / 方案 / 文件）列表（跨对话全局）。 */
  centerTabs: 'movielens.centerTabs',
} as const
