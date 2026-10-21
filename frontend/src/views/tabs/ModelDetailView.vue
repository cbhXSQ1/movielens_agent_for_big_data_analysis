<script setup lang="ts">
// 阶段 C · 中栏「模型详情」标签（资源标签 kind='model'）。
// 经 models store 取模型详情（组件不直接 fetch），整栏铺满中栏：
//   - 指标：按 task_type 复用 results/ml 的结果视图（ClassificationCard / ClusteringPanel /
//     DimReductionPanel）渲染 `model.metrics`（缺失时不造数）。
//   - 产物：按 role 分组（共用 groupArtifactsByRole）；文本类 kind（json/csv/jsonl/md）可预览，
//     `joblib` 等二进制只下载并提示「不支持在线预览」；下载一律走 Agent 模型下载路由。
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NSpin, NTag } from 'naive-ui'
import { useModelsStore } from '@/stores/models'
import { modelDownloadUrl, previewModelArtifact } from '@/api/models'
import type { MlArtifact, MlArtifactKind } from '@/api/models'
import { mlViewFor } from '@/components/results/ml/registry'
import {
  groupArtifactsByRole,
  isTextArtifactKind,
} from '@/components/results/ml/modelArtifacts'
import { formatBytes } from '@/components/results/format'
import MarkdownView from '@/components/results/MarkdownView.vue'

const props = defineProps<{ modelId: string }>()

const models = useModelsStore()
const { current, loading } = storeToRefs(models)

const detailLoading = computed(() => loading.value.detail)
/** 仅当 store 里的详情确属当前编号时才采用（切换标签时避免串模型）。 */
const model = computed(() =>
  current.value && current.value.model_id === props.modelId ? current.value : null,
)
const detailError = computed(() =>
  model.value ? null : models.errors.detail,
)

const mlView = computed(() => mlViewFor(model.value?.task_type))
const metrics = computed(() => model.value?.metrics ?? null)
const groups = computed(() => groupArtifactsByRole(model.value?.artifacts ?? []))

const MAX_PREVIEW_CHARS = 8000

interface PreviewState {
  path: string
  kind: MlArtifactKind
  loading: boolean
  error: string
  content: string
  truncated: boolean
}
const preview = ref<PreviewState | null>(null)

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

function downloadUrl(path: string): string {
  return props.modelId ? modelDownloadUrl(props.modelId, path) : '#'
}

async function openPreview(artifact: MlArtifact): Promise<void> {
  if (!props.modelId) return
  preview.value = {
    path: artifact.path,
    kind: artifact.kind,
    loading: true,
    error: '',
    content: '',
    truncated: false,
  }
  try {
    const text = await previewModelArtifact(props.modelId, artifact.path)
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
      error: err instanceof Error ? err.message : '预览失败',
      content: '',
      truncated: false,
    }
  }
}

watch(
  () => props.modelId,
  (id) => {
    preview.value = null
    if (id) void models.loadModel(id)
  },
  { immediate: true },
)
</script>

<template>
  <section class="model-detail-view" data-testid="model-detail-view">
    <n-spin v-if="detailLoading" data-testid="model-detail-loading" />

    <p
      v-else-if="detailError"
      class="model-detail-view__error"
      data-testid="model-detail-view-error"
    >
      {{ detailError }}
    </p>

    <n-empty
      v-else-if="!model"
      description="模型不存在或已失效"
      data-testid="model-detail-missing"
    />

    <template v-else>
      <header class="model-detail-view__head">
        <h3 class="model-detail-view__title" data-testid="model-detail-title">
          {{ model.model_id }}
        </h3>
        <n-tag size="small" :bordered="false">{{ model.task_type }}</n-tag>
        <span v-if="model.algorithm" class="model-detail-view__meta">
          算法：{{ model.algorithm }}
        </span>
        <span v-if="model.data_version" class="model-detail-view__meta">
          数据版本：{{ model.data_version }}
        </span>
        <span v-if="model.created_at" class="model-detail-view__meta">
          {{ model.created_at }}
        </span>
      </header>

      <section
        v-if="mlView"
        class="model-detail-view__metrics"
        data-testid="model-detail-metrics"
      >
        <component :is="mlView" :metrics="metrics" />
      </section>
      <pre
        v-else-if="metrics"
        class="model-detail-view__code"
        data-testid="model-detail-metrics-raw"
      >{{ JSON.stringify(metrics, null, 2) }}</pre>

      <section
        v-for="group in groups"
        :key="group.role"
        class="model-detail-view__group"
        :data-testid="`model-detail-group-${group.role}`"
      >
        <h4 class="model-detail-view__group-title">{{ group.label }}</h4>
        <ul class="model-detail-view__list">
          <li
            v-for="artifact in group.items"
            :key="artifact.path"
            class="model-detail-view__item"
            :data-testid="`model-detail-artifact-${artifact.path}`"
          >
            <span class="model-detail-view__name" :title="artifact.path">
              {{ artifact.title || artifact.path }}
            </span>
            <n-tag size="small" :bordered="false">{{ artifact.kind }}</n-tag>
            <span class="model-detail-view__size">{{ formatBytes(artifact.size) }}</span>

            <n-button
              v-if="isTextArtifactKind(artifact.kind)"
              size="tiny"
              secondary
              :data-testid="`model-detail-preview-btn-${artifact.path}`"
              @click="openPreview(artifact)"
            >
              预览
            </n-button>
            <span
              v-else
              class="model-detail-view__hint"
              :data-testid="`model-detail-no-preview-${artifact.path}`"
            >
              不支持在线预览
            </span>

            <a
              class="model-detail-view__download"
              :href="downloadUrl(artifact.path)"
              :download="artifact.path"
              :data-testid="`model-detail-download-${artifact.path}`"
            >
              下载
            </a>
          </li>
        </ul>
      </section>

      <div v-if="preview" class="model-detail-view__preview" data-testid="model-detail-preview">
        <div class="model-detail-view__preview-head">
          <span class="model-detail-view__name">{{ preview.path }}</span>
          <n-tag size="small" :bordered="false">{{ preview.kind }}</n-tag>
          <n-tag
            v-if="preview.truncated"
            size="small"
            type="warning"
            :bordered="false"
            data-testid="model-detail-preview-truncated"
          >
            已截断
          </n-tag>
        </div>
        <p v-if="preview.loading" class="model-detail-view__hint">预览加载中…</p>
        <p
          v-else-if="preview.error"
          class="model-detail-view__error"
          data-testid="model-detail-preview-error"
        >
          {{ preview.error }}
        </p>
        <div
          v-else-if="preview.kind === 'md'"
          data-testid="model-detail-preview-content"
        >
          <MarkdownView :content="previewText" />
        </div>
        <pre
          v-else
          class="model-detail-view__code"
          data-testid="model-detail-preview-content"
        >{{ previewText }}</pre>
      </div>
    </template>
  </section>
</template>

<style scoped>
.model-detail-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  min-height: 0;
  padding: 16px;
  box-sizing: border-box;
  overflow: auto;
}
.model-detail-view__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.model-detail-view__title {
  margin: 0;
  font-size: var(--fs-xl);
  font-weight: 600;
}
.model-detail-view__meta {
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-md);
}
.model-detail-view__metrics {
  padding: 12px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.model-detail-view__group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.model-detail-view__group-title {
  margin: 0;
  font-size: var(--fs-md);
  font-weight: 600;
}
.model-detail-view__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.model-detail-view__item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.model-detail-view__name {
  overflow: hidden;
  flex: 1 1 auto;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: var(--fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-detail-view__size {
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.model-detail-view__hint {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.model-detail-view__download {
  font-size: var(--fs-md);
  text-decoration: none;
}
.model-detail-view__preview {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.model-detail-view__preview-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.model-detail-view__code {
  max-height: 420px;
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  border-radius: 6px;
  background: var(--n-code-color, #f6f7f9);
  font-size: var(--fs-sm);
  white-space: pre-wrap;
  word-break: break-all;
}
.model-detail-view__error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--fs-md);
}
</style>
