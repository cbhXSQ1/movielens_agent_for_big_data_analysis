// FB.7 · 连接状态 store（§3.1）。
// 连接与否由两条来源驱动：① `/health` 轮询（REST，见 endpoints.health）；
// ② 当前对话推送流的开/断事件（由 conversations.ts 调 markStreamOpen / markStreamClosed）。
// 另暴露可用工具来源与模型层是否可用，供顶栏与设置面板读取。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { health as fetchHealth } from '@/api/endpoints'
import { isApiError } from '@/api/http'
import type { HealthResponse, ToolSource } from '@/api/types'

export const DEFAULT_POLL_INTERVAL_MS = 10_000

function describeError(err: unknown): string {
  if (isApiError(err)) return err.message
  if (err instanceof Error) return err.message
  return '连接失败'
}

export const useConnectionStore = defineStore('connection', () => {
  const health = ref<HealthResponse | null>(null)
  const healthOk = ref(false)
  const checking = ref(false)
  const streamOpen = ref(false)
  const lastError = ref<string | null>(null)

  const timer = ref<ReturnType<typeof setInterval> | null>(null)

  const connected = computed(() => healthOk.value || streamOpen.value)
  const polling = computed(() => timer.value !== null)
  const service = computed(() => health.value?.service ?? '')
  const scopeDefault = computed(() => health.value?.scope_default ?? '')
  const toolSources = computed<ToolSource[]>(() => health.value?.tools?.sources ?? [])
  const toolsAvailable = computed(() => health.value?.tools?.available ?? false)
  const toolCount = computed(() => health.value?.tools?.count ?? 0)
  const llmSupported = computed(() => health.value?.llm?.supported ?? false)
  const llmConfigured = computed(() => health.value?.llm?.configured ?? false)

  /** 立即探活一次；返回是否在线。 */
  async function check(): Promise<boolean> {
    checking.value = true
    try {
      const res = await fetchHealth()
      health.value = res
      healthOk.value = res.ok !== false
      if (healthOk.value) lastError.value = null
      return healthOk.value
    } catch (err) {
      healthOk.value = false
      lastError.value = describeError(err)
      return false
    } finally {
      checking.value = false
    }
  }

  /** 启动轮询（先立即探活一次）；重复调用不叠加定时器。 */
  function startPolling(intervalMs: number = DEFAULT_POLL_INTERVAL_MS): void {
    if (timer.value) return
    void check()
    timer.value = setInterval(() => {
      void check()
    }, intervalMs)
  }

  function stopPolling(): void {
    if (timer.value) {
      clearInterval(timer.value)
      timer.value = null
    }
  }

  /** 推送流建立（由 conversations.ts 在 onOpen 时调用）。 */
  function markStreamOpen(): void {
    streamOpen.value = true
  }

  /** 推送流断开 / 出错（由 conversations.ts 在 onError / onClose 时调用）。 */
  function markStreamClosed(): void {
    streamOpen.value = false
  }

  function reset(): void {
    stopPolling()
    health.value = null
    healthOk.value = false
    streamOpen.value = false
    lastError.value = null
  }

  return {
    health,
    healthOk,
    checking,
    streamOpen,
    lastError,
    connected,
    polling,
    service,
    scopeDefault,
    toolSources,
    toolsAvailable,
    toolCount,
    llmSupported,
    llmConfigured,
    check,
    startPolling,
    stopPolling,
    markStreamOpen,
    markStreamClosed,
    reset,
  }
})
