// FE-ML-2 · 模型 store（契约 §3.7，直通 REST，不经 LLM）。
// 列表与详情两处加载态 / 错误**各自隔离**：某处失败只在对应处回显，不串到另一处。
// 仅保留可共享的缓存与状态；端点封装一律经 `@/api/models`。

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getModel, listModels } from '@/api/models'
import type { MlModelDetail, MlModelSummary } from '@/api/models'

export type ModelDomain = 'list' | 'detail'

export const useModelsStore = defineStore('models', () => {
  const models = ref<MlModelSummary[]>([])
  const current = ref<MlModelDetail | null>(null)
  const loading = ref<Record<ModelDomain, boolean>>({
    list: false,
    detail: false,
  })
  const errors = ref<Record<ModelDomain, string | null>>({
    list: null,
    detail: null,
  })

  /** §3.7 列模型；失败记列表错误，不抛。 */
  async function loadModels(): Promise<void> {
    loading.value.list = true
    try {
      const res = await listModels()
      models.value = res.models ?? []
      errors.value.list = null
    } catch (err) {
      errors.value.list = err instanceof Error ? err.message : '模型列表拉取失败'
    } finally {
      loading.value.list = false
    }
  }

  /** §3.7 取模型详情；失败置空 current 并记详情错误，不抛。 */
  async function loadModel(id: string): Promise<void> {
    loading.value.detail = true
    try {
      const res = await getModel(id)
      current.value = res.model ?? null
      errors.value.detail = null
    } catch (err) {
      current.value = null
      errors.value.detail = err instanceof Error ? err.message : '模型详情拉取失败'
    } finally {
      loading.value.detail = false
    }
  }

  function clear(): void {
    models.value = []
    current.value = null
    errors.value = { list: null, detail: null }
  }

  return {
    models,
    current,
    loading,
    errors,
    loadModels,
    loadModel,
    clear,
  }
})
