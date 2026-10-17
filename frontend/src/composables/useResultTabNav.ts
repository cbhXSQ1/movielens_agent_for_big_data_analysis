// 阶段 D · 中栏结果标签跳转契约（provide/inject）。
// 壳层（AppShell）持有 activeTab 这一唯一事实源并通过 provide 暴露跳转函数；
// 对话视图里的「内嵌结果卡」（以及后续阶段的证据/报告视图）注入后即可驱动中栏切标签，
// 无需各自维护副本、也不引入新的 store。

import { inject, provide, type InjectionKey } from 'vue'
import type { ResultTabKey } from '@/components/shellConfig'

/** 跳转到某个中栏结果标签。 */
export type ResultTabNavigator = (key: ResultTabKey) => void

/** 注入键。 */
export const RESULT_TAB_NAV: InjectionKey<ResultTabNavigator> = Symbol(
  'movielens.resultTabNav',
)

/** 由壳层调用：登记跳转函数。 */
export function provideResultTabNav(nav: ResultTabNavigator): void {
  provide(RESULT_TAB_NAV, nav)
}

/** 由内容视图调用：取得跳转函数；壳层缺失时返回 null（便于组件独立单测）。 */
export function useResultTabNav(): ResultTabNavigator | null {
  return inject(RESULT_TAB_NAV, null)
}
