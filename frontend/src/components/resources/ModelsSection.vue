<script setup lang="ts">
// FE-ML-2 · 资源面板「模型」段（契约 §3.5 / §3.7 / §4.1 / §4.2）。
// 列表：编号 / 任务类型 / 算法 / 数据版本 / 创建时间 / 指标摘要。
// 详情：产物**按 role 分组**（模型本体 / 群体画像 / 每用户标签 / 用户坐标 / 模型元数据）；
// 每个产物按 **kind** 决定交互：`json`/`csv`/`jsonl`/`md` 可在线预览，
// `joblib` 等二进制只下载（提示"不能在线预览"）。下载一律走模型下载路由（Agent 中继）。
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NSpin, NTag } from 'naive-ui'
import { useModelsStore } from '@/stores/models'
import { modelDownloadUrl, previewModelArtifact } from '@/api/models'
import type { MlArtifact, MlArtifactKind, MlArtifactRole, MlModelSummary } from '@/api/models'
import { formatBytes } from '@/components/results/format'
import MarkdownView from '@/components/results/MarkdownView.vue'
import { describeError } from './resourceUtils'

const models = useModelsStore()
const { models: modelList, current, loading } = storeToRefs(models)

const listLoading = computed(() => loading.value.list)
const detailLoading = computed(() => loading.value.detail)

// 分组顺序与中文名以契约 §4.2 的建议为准；未知 role 归「其它」。
const ROLE_ORDER: MlArtifactRole[] = ['model', 'profile', 'labels', 'coords', 'meta']
const ROLE_LABEL: Record<string, string> = {
  model: '模型本体',
  profile: '群体画像',
  labels: '每用户标签',
  coords: '用户坐标',
  meta: '模型元数据',
}
const TEXT_KINDS = new Set(['json', 'csv', 'jsonl', 'md'])
const MAX_PREVIEW_CHARS = 8000

const groups = computed(() => {
  const artifacts = current.value?.artifacts ?? []
  const byRole = new Map<string, MlArtifact[]>()
  for (const artifact of artifacts) {
    const role = artifact.role || 'other'
    const bucket = byRole.get(role)
    if (bucket) bucket.push(artifact)
    else byRole.set(role, [artifact])
  }
  const ordered: string[] = []
  for (const role of ROLE_ORDER) if (byRole.has(role)) ordered.push(role)
  for (const role of byRole.keys()) if (!ROLE_ORDER.includes(role)) ordered.push(role)
  return ordered.map((role) => ({
    role,
    label: ROLE_LABEL[role] ?? '其它',
    items: byRole.get(role) ?? [],
  }))
})

function summaryEntries(summary: Record<string, unknown> | undefined): Array<[string, string]> {
  if (!summary) return []
  return Object.entries(summary).map(([key, value]) => [key, String(value)])
}

function isTextKind(kind: MlArtifactKind): boolean {
  return TEXT_KINDS.has(kind)
}

function select(model: MlModelSummary): void {
  preview.value = null
  void models.loadModel(model.model_id)
}

function refresh(): void {
  void models.loadModels()
}

function downloadUrl(path: string): string {
  const modelId = current.value?.model_id
  return modelId ? modelDownloadUrl(modelId, path) : '#'
}

interface PreviewState {
  path: string
  kind: MlArtifactKind
  loading: boolean
  error: string
  content: string
  truncated: boolean
}
const preview = ref<PreviewState | null>(null)

async function openPreview(artifact: MlArtifact): Promise<void> {
  const modelId = current.value?.model_id
  if (!modelId) return
  preview.value = {
    path: artifact.path,
    kind: artifact.kind,
    loading: true,
    error: '',
    content: '',
    truncated: false,
  }
  try {
    const text = await previewModelArtifact(modelId, artifact.path)
    const truncated = text.length > MAX_PREVIEW_CHARS
    preview.value = {
      path: artifact.path,
      kind: artifact.kind,
      loading: false,
      error: '',
      content: truncated ? text.slice(0, MAX_PREVIEW_CHARS) : text,
      truncated,
    }
  } catch (err) {
    preview.value = {
      path: artifact.path,
      kind: artifact.kind,
      loading: false,
      error: describeError(err),
      content: '',
      truncated: false,
    }
  }
}

const previewText = computed(() => {
  const state = preview.value
  if (!state || state.error) return ''
  if (state.kind === 'json') {
    try {
      return JSON.stringify(JSON.parse(state.content), null, 2)
    } catch {
      return state.content
    }
  }
  return state.content
})
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
              @click="select(model)"
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

    <div v-if="current" class="model-detail" data-testid="model-detail">
      <div class="model-detail__head">
        <span class="resource-card__name">{{ current.model_id }}</span>
        <n-tag size="small" :bordered="false">{{ current.task_type }}</n-tag>
        <span class="resource-card__size">算法：{{ current.algorithm }}</span>
      </div>

      <n-spin :show="detailLoading">
        <div
          v-for="group in groups"
          :key="group.role"
          class="model-group"
          :data-testid="`model-artifact-group-${group.role}`"
        >
          <h4 class="model-group__title">{{ group.label }}</h4>
          <ul class="resource-list">
            <li
              v-for="artifact in group.items"
              :key="artifact.path"
              class="resource-card resource-card--row"
              :data-testid="`model-artifact-${artifact.path}`"
            >
              <span class="resource-card__name" :title="artifact.path">
                {{ artifact.title || artifact.path }}
              </span>
              <n-tag
                size="small"
                :bordered="false"
                :data-testid="`model-artifact-kind-${artifact.path}`"
              >
                {{ artifact.kind }}
              </n-tag>
              <span class="resource-card__size">{{ formatBytes(artifact.size) }}</span>

              <n-button
                v-if="isTextKind(artifact.kind)"
                size="tiny"
                secondary
                :data-testid="`model-artifact-preview-btn-${artifact.path}`"
                @click="openPreview(artifact)"
              >
                预览
              </n-button>
              <span
                v-else-if="artifact.kind === 'joblib'"
                class="resource-section__hint"
                :data-testid="`model-artifact-no-preview-${artifact.path}`"
              >
                不能在线预览，请下载
              </span>

              <a
                class="model-download"
                :href="downloadUrl(artifact.path)"
                :download="artifact.path"
                :data-testid="`model-artifact-download-${artifact.path}`"
              >
                下载
              </a>
            </li>
          </ul>
        </div>
      </n-spin>

      <div v-if="preview" class="preview" data-testid="model-preview-panel">
        <div class="preview__head">
          <span class="resource-card__name">{{ preview.path }}</span>
          <n-tag size="small" :bordered="false">{{ preview.kind }}</n-tag>
          <n-tag
            v-if="preview.truncated"
            size="small"
            type="warning"
            :bordered="false"
            data-testid="model-preview-truncated"
          >
            已截断
          </n-tag>
        </div>
        <p v-if="preview.loading" class="resource-section__hint">预览加载中…</p>
        <p v-else-if="preview.error" class="resource-error" data-testid="model-preview-error">
          {{ preview.error }}
        </p>
        <div v-else-if="preview.kind === 'md'" data-testid="model-preview-content">
          <MarkdownView :content="previewText" />
        </div>
        <pre v-else class="preview__code" data-testid="model-preview-content">{{ previewText }}</pre>
      </div>
    </div>

    <p
      v-else-if="models.errors.detail && !detailLoading"
      class="resource-error"
      data-testid="model-detail-error"
    >
      {{ models.errors.detail }}
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
  font-size: 15px;
}
.resource-section__hint {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: 12px;
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
.resource-card--row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
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
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 2px;
  color: var(--n-text-color-3, #999);
  font-size: 12px;
}
.resource-card__size {
  color: var(--n-text-color-3, #999);
  font-size: 12px;
}
.model-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
}
.model-summary__item {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--n-action-color, rgba(0, 0, 0, 0.04));
}
.model-detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 6px;
  padding: 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.model-detail__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.model-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}
.model-group__title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
}
.model-download {
  font-size: 13px;
  text-decoration: none;
}
.preview {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.preview__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.preview__code {
  max-height: 300px;
  margin: 0;
  padding: 8px 10px;
  overflow: auto;
  border-radius: 6px;
  background: var(--n-code-color, #f6f7f9);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.resource-error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: 13px;
}
</style>
