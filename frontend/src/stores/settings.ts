// FB.8 · 设置 store：LLM 供应商连接（§3.3 的 `llm` 字段）。
// 仅本地保存（localStorage），**不落服务端**；只在本次发消息时经 `llm` 传出
// （见 chat.send → endpoints.sendByMode）。字段未配齐时 toOverride() 返回 undefined，
// 请求里就不带 llm，让后端用它自己的配置（没配则结构化报错 NOT_CONFIGURED）。
// `enabled` 仅为兼容旧数据保留，不再参与是否使用模型的判断。

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

function hasProviderFields(value: LlmSettings): boolean {
  return (
    value.api_base.trim() !== '' &&
    value.api_key.trim() !== '' &&
    value.model.trim() !== ''
  )
}

function normalize(value: LlmSettings): LlmSettings {
  return { ...value, enabled: hasProviderFields(value) }
}

function readStorage(): LlmSettings {
  const base = defaultLlmSettings()
  try {
    if (typeof localStorage === 'undefined') return base
    const raw = localStorage.getItem(LLM_STORAGE_KEY)
    if (!raw) return base
    const parsed = JSON.parse(raw) as Partial<LlmSettings>
    return normalize({ ...base, ...parsed })
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

  /** 是否配齐（api_base + api_key + model；enabled 不参与）。 */
  const hasOverride = computed(() => hasProviderFields(llm.value))

  function update(patch: Partial<LlmSettings>): void {
    llm.value = normalize({ ...llm.value, ...patch })
    writeStorage(llm.value)
  }

  function save(): void {
    llm.value = normalize(llm.value)
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
