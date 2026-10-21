<script setup lang="ts">
// FG.3 · 清洗后数据段：已发布数据版本（data_version / 来源任务 / 表 / 时间）。
// 经 useResourcesStore（§3.11.2）；无已发布数据时如实空态，不造样例。
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NSpin, NTag } from 'naive-ui'
import { useResourcesStore } from '@/stores/resources'
import { formatBytes } from '@/components/results/format'
import { formatTimestamp } from './resourceUtils'

const resources = useResourcesStore()
const { published, loading } = storeToRefs(resources)

const publishedLoading = computed(() => loading.value.published)
const isEmpty = computed(
  () => !publishedLoading.value && published.value.length === 0,
)

function refresh(): void {
  void resources.loadPublished()
}
</script>

<template>
  <section class="resource-section" data-testid="resources-segment-published">
    <header class="resource-section__head">
      <h3 class="resource-section__title">清洗后数据</h3>
      <n-button size="tiny" quaternary data-testid="published-refresh" @click="refresh">
        刷新
      </n-button>
    </header>

    <n-spin :show="publishedLoading">
      <n-empty
        v-if="isEmpty"
        description="暂无已发布数据"
        data-testid="published-empty"
      />
      <ul v-else class="resource-list" data-testid="published-list">
        <li
          v-for="dataset in published"
          :key="dataset.data_version"
          class="resource-card"
          :data-testid="`published-dataset-${dataset.data_version}`"
        >
          <div class="resource-card__row">
            <span class="resource-card__name">{{ dataset.data_version }}</span>
            <n-tag size="small" type="success" :bordered="false">
              {{ dataset.tables.length }} 表
            </n-tag>
          </div>
          <div class="resource-card__meta">
            <span>来源任务：{{ dataset.source_task_id || '—' }}</span>
            <span>{{ formatTimestamp(dataset.published_at) }}</span>
          </div>
          <ul v-if="dataset.tables.length" class="resource-card__files">
            <li v-for="table in dataset.tables" :key="table.name">
              <span class="resource-card__file">{{ table.name }}</span>
              <span class="resource-card__size">{{ formatBytes(table.size) }}</span>
            </li>
          </ul>
        </li>
      </ul>
    </n-spin>

    <p v-if="resources.errors.published" class="resource-error" data-testid="published-error">
      {{ resources.errors.published }}
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
  gap: 8px;
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
  justify-content: space-between;
  gap: 8px;
}
.resource-card__name {
  overflow: hidden;
  font-size: var(--fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 2px;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.resource-card__files {
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
}
.resource-card__files li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--fs-md);
}
.resource-card__file {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__size {
  color: var(--n-text-color-3, #999);
}
.resource-error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--fs-md);
}
</style>
