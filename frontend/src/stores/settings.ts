// FB.8 · 设置 store：LLM 供应商连接（§3.3 的 `llm` 字段）。
// 默认值来自前端 `.env.local`，当前表单值常驻内存；
// 命名档案保存到后端，同名保存覆盖旧档案，并可重新载入。
// 只在发消息时经 `llm` 传出（见 chat.send → endpoints.sendByMode）。
// 字段未配齐时 toOverride() 返回 undefined，请求里就不带 llm，
// 让后端用它自己的配置（没配则结构化报错 NOT_CONFIGURED）。
// `enabled` 仅为兼容旧数据保留，不再参与是否使用模型的判断。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  deleteProvider,
  listProviders,
  saveProvider as postProvider,
} from '@/api/llmProviders'
import type { LlmConfigOverride, LlmProvider } from '@/api/types'

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
  const providers = ref<LlmProvider[]>([])

  /** 是否配齐（api_base + api_key + model；enabled 不参与）。 */
  const hasOverride = computed(() => hasProviderFields(llm.value))

  function update(patch: Partial<LlmSettings>): void {
    llm.value = normalize({ ...llm.value, ...patch })
  }

  /** 读取后端长期保存的供应商档案。 */
  async function loadProviders(): Promise<LlmProvider[]> {
    const res = await listProviders()
    providers.value = res.providers ?? []
    return providers.value
  }

  /** 用当前连接字段按名称保存；同名由后端覆盖，随后刷新列表。 */
  async function saveProvider(name: string): Promise<LlmProvider> {
    const trimmed = name.trim()
    if (!trimmed) throw new Error('供应商名称不能为空')

    save()
    const res = await postProvider({
      name: trimmed,
      api_base: llm.value.api_base.trim(),
      api_key: llm.value.api_key.trim(),
      model: llm.value.model.trim(),
      mode: llm.value.mode,
    })
    await loadProviders()
    return res.provider
  }

  /** 把某条已保存档案填回当前连接字段。 */
  function applyProvider(name: string): boolean {
    const hit = providers.value.find((item) => item.name === name)
    if (!hit) return false
    llm.value = normalize({
      ...llm.value,
      api_base: hit.api_base,
      api_key: hit.api_key,
      model: hit.model,
      mode: hit.mode || 'llm',
    })
    return true
  }

  /** 删除一条已保存档案，随后刷新列表。 */
  async function removeProvider(name: string): Promise<void> {
    await deleteProvider(name)
    await loadProviders()
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

  return {
    llm,
    providers,
    hasOverride,
    update,
    save,
    load,
    reset,
    toOverride,
    loadProviders,
    saveProvider,
    applyProvider,
    removeProvider,
  }
})
