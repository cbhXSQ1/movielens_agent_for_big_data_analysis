// 阶段 C · 中栏「资源标签」共享类型与 key 构造。
// 资源（模型 / 方案 / 文件）不再只在左栏内联预览，而是作为中栏的一个标签打开；
// 标签种类（kind）与渲染组件解耦，经注册表 `centerTabViews.ts` 分发（可插拔、易扩展）。
//
// key 统一带 `resource:` 前缀（URL-ish），与产物标签的 id（如 `scheme:xxx.json`、
// `task:t1:report.md`）互不冲突，保证合并进同一标签栏后 key 稳定且不撞车。

/** 资源标签种类：模型 / 方案文件 / 产出文件。 */
export type ResourceTabKind = 'model' | 'scheme' | 'file'

/** 中栏资源标签描述（label 用于标签文案，payload 为渲染组件的 props）。 */
export interface ShellTab {
  /** 稳定、唯一的标签 key（见下方 key 构造）。 */
  key: string
  /** 标签文案。 */
  label: string
  /** 标签种类（决定用哪个渲染组件）。 */
  kind: ResourceTabKind
  /** 渲染组件的 props（如 model → { modelId }；file → { taskId, path }）。 */
  payload: Record<string, unknown>
}

/** 模型标签 key：`resource:model:<modelId>`。 */
export function modelTabKey(modelId: string): string {
  return `resource:model:${encodeURIComponent(modelId)}`
}

/** 方案标签 key：`resource:scheme:<name>`。 */
export function schemeTabKey(name: string): string {
  return `resource:scheme:${encodeURIComponent(name)}`
}

/** 文件标签 key：`resource:file:<taskId>:<path>`（任务号与路径各自编码，避免分隔符歧义）。 */
export function fileTabKey(taskId: string, path: string): string {
  return `resource:file:${encodeURIComponent(taskId)}:${encodeURIComponent(path)}`
}
