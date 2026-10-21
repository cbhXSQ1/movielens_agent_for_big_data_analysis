<script setup lang="ts">
// 迭代二 · 已登记模型（真实数据）。
// 数据来自能力服务的 `list_ml_models`（经 /api/models）；点「查看」在中栏打开模型详情标签。
// 当前任务结果含 ML 板块时，先渲染结果视图，再列出已登记模型供浏览。
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useModelsStore } from '@/stores/models'
import { useTasksStore } from '@/stores/tasks'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { modelTab } from '@/components/centerTabViews'
import { useTreeWindowOpen } from '@/composables/useTreeWindowOpen'
import { useResultBlocks } from '@/composables/useResultBlocks'
import { isRecord } from '@/components/results/blocks'
import { formatPercent } from '@/utils/format'
import MlResultView from '@/components/results/ml/MlResultView.vue'

const store = useModelsStore()
const tasks = useTasksStore()
const centerTabs = useCenterTabsStore()
const { models, loading, errors } = storeToRefs(store)
const { currentResult } = storeToRefs(tasks)
const { blocks } = useResultBlocks(() => tasks.currentResult)
const openTree = useTreeWindowOpen()

const hasMlResult = computed(() => {
  const b = blocks.value
  if (!b) return false
  return (
    isRecord(b.classification) ||
    isRecord(b.clustering) ||
    isRecord(b.dimensionality_reduction)
  )
})

onMounted(() => {
  if (models.value.length === 0) void store.loadModels()
})

const TYPE_LABEL: Record<string, string> = {
  classification: '分类',
  clustering: '聚类',
  dimensionality_reduction: '降维',
}

function typeLabel(value: string): string {
  return TYPE_LABEL[value] ?? value
}

const RATE_KEYS = new Set(['f1', 'auc', 'precision', 'recall', 'pr_auc', 'silhouette'])
const INT_KEYS = new Set(['k', 'n_components'])

function formatSummaryValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (RATE_KEYS.has(key)) {
    const text = formatPercent(value as number | string | null | undefined)
    if (text) return text
  }
  if (INT_KEYS.has(key)) {
    if (typeof value === 'number' && Number.isFinite(value)) return String(value)
    if (typeof value === 'string') return value
  }
  if (typeof value === 'number' || typeof value === 'string' || typeof value === 'boolean') {
    return String(value)
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function summaryPairs(summary: Record<string, unknown> | undefined): Array<[string, string]> {
  if (!summary) return []
  return Object.entries(summary).map(([key, value]) => [
    key,
    formatSummaryValue(key, value),
  ])
}

function openModel(model: { model_id: string; algorithm?: string | null }): void {
  centerTabs.open(modelTab(model))
}

async function copyId(id: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(id)
  } catch {
    /* 剪贴板不可用时静默 */
  }
}
</script>

<template>
  <section class="view active" data-view="ml" data-testid="view-ml">
    <div class="doc">
      <div class="doc-head">
        <div>
          <h2>迭代二 · 已登记模型</h2>
          <div class="panel-sub">分类 / 聚类 / 降维 · 指标 0–1，前端按需 ×100</div>
        </div>
        <div class="actions">
          <button class="btn sm" data-testid="ml-refresh" @click="store.loadModels()">刷新</button>
          <button
            v-if="openTree"
            class="btn sm soft"
            data-testid="ml-open-tree"
            @click="openTree()"
          >
            在对话树中继续
          </button>
        </div>
      </div>

      <div v-if="hasMlResult" class="card" data-testid="ml-current-result">
        <h3>当前任务结果</h3>
        <MlResultView :result="currentResult" />
      </div>

      <p v-if="loading.list" class="panel-sub" data-testid="ml-loading">正在加载模型…</p>
      <p v-else-if="errors.list" class="panel-sub" data-testid="ml-error">{{ errors.list }}</p>
      <p v-else-if="models.length === 0" class="panel-sub" data-testid="ml-empty">
        暂无模型。模型由能力服务登记：需要虚拟机侧能力服务在线，并已训练过模型。
      </p>

      <div v-else class="ml-grid">
        <div
          v-for="m in models"
          :key="m.model_id"
          class="card"
          :data-testid="`ml-model-${m.model_id}`"
        >
          <h3>{{ typeLabel(m.task_type) }} · {{ m.algorithm }}</h3>
          <p class="panel-sub">{{ m.model_id }} · data_version {{ m.data_version }}</p>
          <div class="metric-grid">
            <div
              v-for="pair in summaryPairs(m.metrics_summary)"
              :key="pair[0]"
              class="metric-box"
            >
              <div class="v">{{ pair[1] }}</div>
              <div class="l">{{ pair[0] }}</div>
            </div>
          </div>
          <div class="actions">
            <button
              class="btn sm"
              :data-testid="`ml-open-${m.model_id}`"
              @click="openModel(m)"
            >
              查看
            </button>
            <button
              class="btn sm soft"
              :data-testid="`ml-copy-${m.model_id}`"
              @click="copyId(m.model_id)"
            >
              复制 model_id
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
