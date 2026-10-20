// FB.8 · 设置 store：LLM 供应商连接（§3.3 的 `llm` 字段）。
// 默认值来自前端 `.env.local`，只在内存中保存；不写浏览器存储，也不落服务端。
// 只在本次发消息时经 `llm` 传出（见 chat.send → endpoints.sendByMode）。
// 字段未配齐时 toOverride() 返回 undefined，请求里就不带 llm，
// 让后端用它自己的配置（没配则结构化报错 NOT_CONFIGURED）。
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

export function defaultLlmSettings(): LlmSettings {
  return {
    enabled: false,
    api_base: import.meta.env.VITE_LLM_API_BASE?.trim() ?? '',
    api_key: import.meta.env.VITE_LLM_API_KEY?.trim() ?? '',
    model: import.meta.env.VITE_LLM_MODEL?.trim() ?? '',
    mode: import.meta.env.VITE_LLM_MODE?.trim() || 'llm',
  }
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

export const useSettingsStore = defineStore('settings', () => {
  const llm = ref<LlmSettings>(defaultLlmSettings())

  /** 是否配齐（api_base + api_key + model；enabled 不参与）。 */
  const hasOverride = computed(() => hasProviderFields(llm.value))

  function update(patch: Partial<LlmSettings>): void {
    llm.value = normalize({ ...llm.value, ...patch })
  }

  function save(): void {
    llm.value = normalize(llm.value)
  }

  /** 重新应用前端 `.env.local` 的默认值。 */
  function load(): void {
    llm.value = defaultLlmSettings()
  }

  function reset(): void {
    llm.value = defaultLlmSettings()
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
