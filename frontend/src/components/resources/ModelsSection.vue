<script setup lang="ts">
// FE-ML-2 · 资源面板「模型」段（契约 §3.5 / §3.7 / §4.1 / §4.2）。
// 列表：编号 / 任务类型 / 算法 / 数据版本 / 创建时间 / 指标摘要。
// 「查看」把模型作为**中栏资源标签**打开（不再在左栏内联详情 / 产物预览，改由
// 中栏 ModelDetailView 承担）；本段只负责列表与刷新，保持左栏干净。
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NSpin, NTag } from 'naive-ui'
import { useModelsStore } from '@/stores/models'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { modelTab } from '@/components/centerTabViews'
import type { MlModelSummary } from '@/api/models'

const models = useModelsStore()
const centerTabs = useCenterTabsStore()
const { models: modelList, loading } = storeToRefs(models)

const listLoading = computed(() => loading.value.list)

function summaryEntries(summary: Record<string, unknown> | undefined): Array<[string, string]> {
  if (!summary) return []
  return Object.entries(summary).map(([key, value]) => [key, String(value)])
}

/** 「查看」：把该模型登记为中栏资源标签（按 modelId 去重）。 */
function openModel(model: MlModelSummary): void {
  centerTabs.open(modelTab(model))
}

function refresh(): void {
  void models.loadModels()
}
</script>

<template>
  <section class="resource-section" data-testid="resources-segment-models">
    <header class="resource-section__head">
      <h3 class="resource-section__title">模型</h3>
      <n-button size="tiny" quaternary data-testid="models-refresh" @click="refresh">
        刷新
      </n-button>
    </header>

    <n-spin :show="listLoading">
      <n-empty
        v-if="!listLoading && !modelList.length"
        description="暂无模型"
        data-testid="models-empty"
      />
      <ul v-else class="resource-list" data-testid="models-list">
        <li
          v-for="model in modelList"
          :key="model.model_id"
          class="resource-card"
          :data-testid="`model-row-${model.model_id}`"
        >
          <div class="resource-card__row">
            <span class="resource-card__name" :title="model.model_id">
              {{ model.model_id }}
            </span>
            <n-tag size="small" :bordered="false">{{ model.task_type }}</n-tag>
            <n-button
              size="tiny"
              secondary
              :data-testid="`model-select-${model.model_id}`"
              @click="openModel(model)"
            >
              查看
            </n-button>
          </div>
          <div class="resource-card__meta">
            <span>算法：{{ model.algorithm || '—' }}</span>
            <span>数据版本：{{ model.data_version || '—' }}</span>
            <span>{{ model.created_at || '—' }}</span>
          </div>
          <div
            v-if="summaryEntries(model.metrics_summary).length"
            class="model-summary"
            :data-testid="`model-summary-${model.model_id}`"
          >
            <span
              v-for="[key, value] in summaryEntries(model.metrics_summary)"
              :key="key"
              class="model-summary__item"
            >
              {{ key }}={{ value }}
            </span>
          </div>
        </li>
      </ul>
    </n-spin>

    <p v-if="models.errors.list" class="resource-error" data-testid="models-error">
      {{ models.errors.list }}
    </p>
  </section>
</template>

<style scoped>
.resource-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
}
.resource-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.resource-section__title {
  margin: 0;
  font-size: var(--fs-lg);
}
.resource-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.resource-card {
  padding: 8px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.resource-card__row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.resource-card__name {
  overflow: hidden;
  flex: 1 1 auto;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: var(--fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 2px;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.model-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
  font-size: var(--fs-sm);
}
.model-summary__item {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--n-action-color, rgba(0, 0, 0, 0.04));
}
.resource-error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--fs-md);
}
</style>
