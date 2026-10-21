// 阶段 C · FC.4：窄屏判定。
// 窗口宽度 ≤ 阈值（默认 1100px）时视为窄屏，左栏改为抽屉。
// 用 window.innerWidth + resize 监听实现，避免依赖 matchMedia，便于测试注入宽度。

import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

/** 窄屏阈值（px）。 */
export const NARROW_BREAKPOINT = 1100

export function useNarrowScreen(threshold: number = NARROW_BREAKPOINT): Ref<boolean> {
  const isNarrow = ref(false)

  function update(): void {
    if (typeof window === 'undefined') return
    isNarrow.value = window.innerWidth <= threshold
  }

  // 首次同步（挂载前即可得到正确初值，避免首帧闪动）。
  update()

  onMounted(() => {
    update()
    window.addEventListener('resize', update)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('resize', update)
  })

  return isNarrow
}
