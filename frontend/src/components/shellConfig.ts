// 新前端 · 外壳的共享常量与类型。
// 左栏四个面板 key；中栏结果标签固定为 7 个（对话 / 五维评分 / 评估报告 /
// metrics.json / 方案依据 / 迭代二模型 / 知识图谱），与 HTML 原型一致。

/** 左栏面板标识（①对话列表 ②对话树 ③资源 ④任务进度）。 */
export type PanelKey = 'list' | 'tree' | 'resources' | 'progress'

/**
 * 中栏结果标签标识。固定 7 个：chat / score / report / metrics / basis / ml / kg。
 * 保持 string 类型以兼容对话内产物链接的动态导航。
 */
export type ResultTabKey = string

/** 固定标签的联合字面量（用于类型校验）。 */
export type FixedTabKey = 'chat' | 'score' | 'report' | 'metrics' | 'basis' | 'ml' | 'kg'

export interface ShellPanelDef {
  key: PanelKey
  label: string
}

export interface ShellTabDef {
  key: ResultTabKey
  label: string
  /** 是否显示关闭按钮（「对话」不可关；其余可关）。 */
  closable?: boolean
}

/** 左栏图标条顺序与文案。 */
export const LEFT_PANELS: readonly ShellPanelDef[] = [
  { key: 'list', label: '对话列表' },
  { key: 'tree', label: '对话树' },
  { key: 'resources', label: '资源' },
  { key: 'progress', label: '任务进度' },
]

/** 左栏面板可拖拽宽度的边界与默认值（px）。 */
export const LEFT_RAIL_WIDTH = {
  /** 最窄。 */
  min: 240,
  /** 最宽。 */
  max: 420,
  /** 默认宽度（与原型一致）。 */
  default: 290,
  /** 键盘方向键每次调整的步长。 */
  step: 16,
} as const

/** 中栏固定 7 个标签（顺序与原型一致）。 */
export const FIXED_TABS: readonly ShellTabDef[] = [
  { key: 'chat', label: '对话' },
  { key: 'score', label: '五维评分', closable: true },
  { key: 'report', label: '评估报告.md', closable: true },
  { key: 'metrics', label: 'metrics.json', closable: true },
  { key: 'basis', label: '方案依据', closable: true },
  { key: 'ml', label: '迭代二模型', closable: true },
  { key: 'kg', label: '知识图谱', closable: true },
]

/** 基础「对话」标签。 */
export const CHAT_TAB: ShellTabDef = FIXED_TABS[0]

/** 壳层状态持久化键（localStorage）。 */
export const SHELL_STORAGE_KEYS = {
  /** 左栏展开/收起：'1' 收起、'0' 展开。 */
  collapsed: 'movielens.shell.collapsed',
  /** 左栏当前面板。 */
  panel: 'movielens.shell.panel',
  /** 左栏内联面板宽度。 */
  leftWidth: 'movielens.shell.leftWidth',
  /** 中栏当前标签。 */
  tab: 'movielens.shell.tab',
  /** 中栏已打开的可关闭标签集合（JSON 数组，刷新后恢复）。 */
  openTabs: 'movielens.shell.openTabs',
  /** 右栏收起状态（宽屏）：'1' 收起。 */
  rightCollapsed: 'movielens.shell.rightCollapsed',
  /** 当前对话编号。 */
  conversation: 'movielens.currentConversation',
  /** 中栏「资源标签」（模型 / 方案 / 文件）列表（跨对话全局）。 */
  centerTabs: 'movielens.centerTabs',
} as const

/** 可关闭的标签 key 集合（除 chat 外）。 */
export const CLOSABLE_TAB_KEYS: readonly ResultTabKey[] = [
  'score',
  'report',
  'metrics',
  'basis',
  'ml',
  'kg',
]
