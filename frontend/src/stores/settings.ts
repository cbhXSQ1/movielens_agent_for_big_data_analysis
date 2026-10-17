// FB.8 · 设置 store：LLM 配置（§3.3 的 `llm` 字段）。
// 仅本地保存（localStorage），**不落服务端**；只在本次发消息时经 `llm` 传出
// （见 chat.send → endpoints.sendByMode）。未配齐时 toOverride() 返回 undefined，
// 请求里就不带 llm，让后端用它自己的配置（没配则结构化报错 NOT_CONFIGURED）。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { LlmConfigOverride } from '@/api/types'

export interface LlmSettings {
  enabled: boolean
  api_base: string
  api_key: string
  model: string
  mode: string
}

export const LLM_STORAGE_KEY = 'movielens.llm.config'

export function defaultLlmSettings(): LlmSettings {
  return { enabled: false, api_base: '', api_key: '', model: '', mode: 'llm' }
}

function readStorage(): LlmSettings {
  const base = defaultLlmSettings()
  try {
    if (typeof localStorage === 'undefined') return base
    const raw = localStorage.getItem(LLM_STORAGE_KEY)
    if (!raw) return base
    const parsed = JSON.parse(raw) as Partial<LlmSettings>
    return { ...base, ...parsed }
  } catch {
    return base
  }
}

function writeStorage(value: LlmSettings): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(LLM_STORAGE_KEY, JSON.stringify(value))
    }
  } catch {
    /* 存储不可用时静默忽略 */
  }
}

export const useSettingsStore = defineStore('settings', () => {
  const llm = ref<LlmSettings>(readStorage())

  /** 是否配齐（enabled + api_base + api_key + model）。 */
  const hasOverride = computed(
    () =>
      llm.value.enabled &&
      llm.value.api_base.trim() !== '' &&
      llm.value.api_key.trim() !== '' &&
      llm.value.model.trim() !== '',
  )

  function update(patch: Partial<LlmSettings>): void {
    llm.value = { ...llm.value, ...patch }
    writeStorage(llm.value)
  }

  function save(): void {
    writeStorage(llm.value)
  }

  /** 从 localStorage 重新读取（如外部改动后）。 */
  function load(): void {
    llm.value = readStorage()
  }

  function reset(): void {
    llm.value = defaultLlmSettings()
    writeStorage(llm.value)
  }

  /** 发消息时传给后端的 llm 覆盖；未配齐返回 undefined。 */
  function toOverride(): LlmConfigOverride | undefined {
    if (!hasOverride.value) return undefined
    return {
      enabled: true,
      api_base: llm.value.api_base.trim(),
      api_key: llm.value.api_key.trim(),
      model: llm.value.model.trim(),
      mode: llm.value.mode,
    }
  }

  return { llm, hasOverride, update, save, load, reset, toOverride }
})
