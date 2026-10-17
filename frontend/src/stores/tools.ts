// FB.7 · 工具表 store（§3.7）。
// 主来源是推送流上的 `tools_snapshot`（由 conversations.ts 分流到 applySnapshot）；
// `load()` 走 `GET /api/tools` 只读兜底（前端一般不主动拉）。
// 离线表单据此取参数清单与默认值（parametersOf / defaultsFor）。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchTools } from '@/api/endpoints'
import type { ToolParamEntry, ToolSpec, ToolSource } from '@/api/types'

export const useToolsStore = defineStore('tools', () => {
  const tools = ref<ToolSpec[]>([])
  const loading = ref(false)
  const lastError = ref<string | null>(null)

  const count = computed(() => tools.value.length)
  const names = computed(() => tools.value.map((t) => t.name))
  const sources = computed<ToolSource[]>(() =>
    Array.from(new Set(tools.value.map((t) => t.source))),
  )
  const byName = computed<Record<string, ToolSpec>>(() => {
    const map: Record<string, ToolSpec> = {}
    for (const t of tools.value) map[t.name] = t
    return map
  })

  /** 整表替换（`tools_snapshot` 为完整工具表，非增量）。 */
  function applySnapshot(list: ToolSpec[]): void {
    tools.value = Array.isArray(list) ? [...list] : []
    lastError.value = null
  }

  /** 只读兜底拉取。 */
  async function load(): Promise<ToolSpec[]> {
    loading.value = true
    try {
      const res = await fetchTools()
      applySnapshot(res.tools ?? [])
      return tools.value
    } catch (err) {
      lastError.value = err instanceof Error ? err.message : '工具表拉取失败'
      return tools.value
    } finally {
      loading.value = false
    }
  }

  function get(name: string): ToolSpec | null {
    return byName.value[name] ?? null
  }

  function parametersOf(name: string): ToolParamEntry[] {
    return get(name)?.parameters ?? []
  }

  /** 离线表单预填：把参数里带 `default` 的项收成对象。 */
  function defaultsFor(name: string): Record<string, unknown> {
    const spec = get(name)
    const out: Record<string, unknown> = {}
    if (!spec) return out
    for (const param of spec.parameters) {
      if (param.default !== undefined) out[param.name] = param.default
    }
    return out
  }

  function reset(): void {
    tools.value = []
    lastError.value = null
  }

  return {
    tools,
    loading,
    lastError,
    count,
    names,
    sources,
    byName,
    applySnapshot,
    load,
    get,
    parametersOf,
    defaultsFor,
    reset,
  }
})
