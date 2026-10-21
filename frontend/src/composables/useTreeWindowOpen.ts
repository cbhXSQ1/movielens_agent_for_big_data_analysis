// 新前端 · 对话树浮窗打开契约（provide/inject）。
// 壳层（AppShell）持有 treeOpen 状态并通过 provide 暴露打开函数；
// 左栏对话树面板、对话内「打开对话树」、知识图谱「在对话树中继续」注入后即可驱动浮窗。

import { inject, provide, type InjectionKey } from 'vue'

/** 打开对话树浮窗（可指定跳到的节点，默认当前）。 */
export type TreeWindowOpener = (nodeId?: number) => void

/** 注入键。 */
export const TREE_WINDOW_OPEN: InjectionKey<TreeWindowOpener> = Symbol(
  'movielens.treeWindowOpen',
)

/** 由壳层调用：登记打开函数。 */
export function provideTreeWindowOpen(opener: TreeWindowOpener): void {
  provide(TREE_WINDOW_OPEN, opener)
}

/** 由内容视图调用：取得打开函数；壳层缺失时返回 null。 */
export function useTreeWindowOpen(): TreeWindowOpener | null {
  return inject(TREE_WINDOW_OPEN, null)
}
