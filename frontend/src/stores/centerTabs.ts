// 阶段 C · 中栏「资源标签」store。
// 资源（模型 / 方案 / 文件）作为中栏标签打开时登记于此：按 key 去重，可关闭、可列举。
// 标签集合是**跨对话全局**的（资源与对话无绑定）；持久化到 localStorage，刷新后恢复。
// 存储不可用（隐私模式 / 配额）时一律静默容错，不影响主流程。

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { SHELL_STORAGE_KEYS } from '@/components/shellConfig'
import type { ResourceTabKind, ShellTab } from '@/components/centerTabs'

const KINDS: readonly ResourceTabKind[] = ['model', 'scheme', 'file']

/** 宽松校验：只接受形状完整的标签，破损条目丢弃（不因一处坏数据丢整份）。 */
function isShellTab(value: unknown): value is ShellTab {
  if (!value || typeof value !== 'object') return false
  const tab = value as Record<string, unknown>
  return (
    typeof tab.key === 'string' &&
    tab.key !== '' &&
    typeof tab.label === 'string' &&
    typeof tab.kind === 'string' &&
    (KINDS as string[]).includes(tab.kind) &&
    !!tab.payload &&
    typeof tab.payload === 'object'
  )
}

/** 从 localStorage 读回标签列表（刷新后恢复）；任何异常回落空列表。 */
function readTabs(): ShellTab[] {
  try {
    if (typeof localStorage === 'undefined') return []
    const raw = localStorage.getItem(SHELL_STORAGE_KEYS.centerTabs)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isShellTab)
  } catch {
    return []
  }
}

export const useCenterTabsStore = defineStore('centerTabs', () => {
  const tabs = ref<ShellTab[]>(readTabs())

  /** 把标签列表写回 localStorage（存储不可用时静默忽略）。 */
  function persist(): void {
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(SHELL_STORAGE_KEYS.centerTabs, JSON.stringify(tabs.value))
      }
    } catch {
      /* 存储不可用时静默忽略 */
    }
  }

  /** 打开（登记）一个资源标签：按 key 去重（已存在则原位置就地刷新，不重复、不改序）。 */
  function open(tab: ShellTab): void {
    if (!tab || !tab.key) return
    const existing = tabs.value.findIndex((t) => t.key === tab.key)
    if (existing >= 0) {
      tabs.value[existing] = tab
    } else {
      tabs.value.push(tab)
    }
    persist()
  }

  /** 关闭（移除）某 key 的标签；不存在则幂等。 */
  function close(key: string): void {
    const idx = tabs.value.findIndex((t) => t.key === key)
    if (idx < 0) return
    tabs.value.splice(idx, 1)
    persist()
  }

  /** 某 key 的标签是否已打开。 */
  function isOpen(key: string): boolean {
    return tabs.value.some((t) => t.key === key)
  }

  /** 当前全部资源标签（同一引用，供视图直接渲染）。 */
  function list(): ShellTab[] {
    return tabs.value
  }

  /** 清空全部资源标签。 */
  function clear(): void {
    tabs.value = []
    persist()
  }

  return {
    tabs,
    open,
    close,
    isOpen,
    list,
    clear,
  }
})
