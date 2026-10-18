<script setup lang="ts">
// 动态产物标签内容（§3.12）。按 `source` / `kind` 渲染一条产物：
//   - scheme                              → 复用方案渲染（SchemeView：维度/指标/规则/局限）
//   - task_file + md                      → Markdown 渲染（复用 MarkdownView）
//   - task_file + json                    → 美化 JSON（可折叠）
//   - task_file + text                    → 纯文本
//   - task_file + dat/other               → 二进制 / 大文件提示 + 下载
//   - 一律提供下载（§3.11.4 fileDownloadUrl；scheme 无下载端点则只展示内容）
// 取内容一律经 resources store（组件不直接 fetch）。
import { computed, ref, watch } from 'vue'
import { NButton, NEmpty, NSpin, NTag } from 'naive-ui'
import MarkdownView from '@/components/results/MarkdownView.vue'
import SchemeView from '@/components/basis/SchemeView.vue'
import { categoryTagType, isLogArtifact } from '@/components/results/artifactCategory'
import { useResourcesStore } from '@/stores/resources'
import type { ArtifactRef } from '@/api/types'

const props = defineProps<{
  artifact: ArtifactRef | null
}>()

const resources = useResourcesStore()

const loading = ref(false)
const error = ref<string | null>(null)
/** task_file 的文本预览内容。 */
const text = ref('')
/** task_file 是否可预览（json/md/text）。 */
const previewable = ref(false)
/** scheme 内容（经 resources.loadSchemeContent）。 */
const schemeContent = ref<unknown>(null)
const schemeLoaded = ref(false)

const isScheme = computed(() => props.artifact?.source === 'scheme')
const isTaskFile = computed(() => props.artifact?.source === 'task_file')
const kind = computed(() => String(props.artifact?.kind ?? ''))
const isTextKind = computed(() =>
  ['json', 'md', 'text'].includes(kind.value),
)

/** 类别徽标配色（未知类别回落 default）。 */
const categoryType = computed(() => categoryTagType(props.artifact?.category))

/** 日志类产物（类别为日志 / 路径在 logs/ 下 / driver.log 式文件名）默认折叠。 */
const isLog = computed(() => (props.artifact ? isLogArtifact(props.artifact) : false))

const downloadUrl = computed(() => {
  const a = props.artifact
  // `a.task_id` 为 Hadoop 任务编号（非 Agent 任务号）；下载经 Agent 中转（/api/files/download）。
  if (!a || a.source !== 'task_file' || !a.task_id || !a.path) return null
  return resources.downloadUrl(a.task_id, a.path)
})

const prettyJson = computed(() => {
  if (!text.value) return ''
  try {
    return JSON.stringify(JSON.parse(text.value), null, 2)
  } catch {
    return text.value
  }
})

function reset(): void {
  loading.value = false
  error.value = null
  text.value = ''
  previewable.value = false
  schemeContent.value = null
  schemeLoaded.value = false
}

async function load(a: ArtifactRef): Promise<void> {
  if (a.source === 'scheme') {
    const name = a.scheme_name
    if (!name) {
      error.value = '该方案未给出文件名，无法取内容'
      return
    }
    loading.value = true
    try {
      const res = await resources.loadSchemeContent(name)
      schemeContent.value = res.content
      schemeLoaded.value = true
      error.value = null
    } catch (err) {
      error.value = err instanceof Error ? err.message : '方案内容拉取失败'
    } finally {
      loading.value = false
    }
    return
  }

  if (a.source === 'task_file') {
    if (!a.task_id || !a.path) {
      error.value = '该产物缺少任务编号或路径，无法取内容'
      return
    }
    if (!isTextKind.value) return
    loading.value = true
    try {
      const res = await resources.preview(a.task_id, a.path)
      previewable.value = res.previewable !== false && typeof res.content === 'string'
      text.value = typeof res.content === 'string' ? res.content : ''
      error.value = null
    } catch (err) {
      error.value = err instanceof Error ? err.message : '预览失败'
    } finally {
      loading.value = false
    }
  }
}

watch(
  () => props.artifact?.id,
  () => {
    reset()
    const a = props.artifact
    if (a) void load(a)
  },
  { immediate: true },
)
</script>

<template>
  <section class="artifact-view" data-testid="artifact-view">
    <n-empty
      v-if="!artifact"
      description="产物不存在或已失效"
      data-testid="artifact-missing"
    />

    <template v-else>
      <header class="artifact-view__head">
        <h3 class="artifact-view__title" data-testid="artifact-title">
          {{ artifact.title || artifact.id }}
        </h3>
        <n-tag
          v-if="artifact.category"
          size="small"
          :bordered="false"
          :type="categoryType"
          data-testid="artifact-category-badge"
        >
          {{ artifact.category }}
        </n-tag>
        <n-tag size="small" :bordered="false">{{ artifact.kind }}</n-tag>
        <a
          v-if="downloadUrl"
          class="artifact-view__download"
          :href="downloadUrl"
          :download="artifact.path ?? artifact.title"
          data-testid="artifact-download"
        >
          <n-button size="tiny" secondary>下载</n-button>
        </a>
      </header>

      <n-spin v-if="loading" data-testid="artifact-loading" />

      <p v-else-if="error" class="artifact-view__error" data-testid="artifact-error">
        {{ error }}
      </p>

      <!-- 日志类默认折叠；其余类别直接展开（同一份渲染链，仅外层容器不同）。 -->
      <component
        :is="isLog ? 'details' : 'div'"
        v-else
        class="artifact-view__body"
        :data-testid="isLog ? 'artifact-log-collapsed' : 'artifact-body'"
      >
        <summary v-if="isLog" class="artifact-view__log-summary">
          日志内容
        </summary>

        <!-- scheme：复用方案渲染 -->
        <SchemeView
          v-if="isScheme && schemeLoaded"
          :content="schemeContent"
          data-testid="artifact-scheme"
        />

        <!-- task_file + md -->
        <MarkdownView
          v-else-if="isTaskFile && kind === 'md' && previewable"
          :content="text"
          data-testid="artifact-markdown"
        />

        <!-- task_file + json：美化 JSON（可折叠） -->
        <details
          v-else-if="isTaskFile && kind === 'json' && previewable"
          class="artifact-view__json"
          open
          data-testid="artifact-json"
        >
          <summary>JSON 内容</summary>
          <pre class="artifact-view__code">{{ prettyJson }}</pre>
        </details>

        <!-- task_file + text -->
        <pre
          v-else-if="isTaskFile && kind === 'text' && previewable"
          class="artifact-view__code"
          data-testid="artifact-text"
        >{{ text }}</pre>

        <!-- task_file + dat/other：二进制 / 大文件，仅提示 + 下载 -->
        <div
          v-else-if="isTaskFile && !isTextKind"
          class="artifact-view__binary"
          data-testid="artifact-binary"
        >
          <p class="artifact-view__hint">
            该产物为二进制 / 大文件（{{ artifact.kind }}），不支持在线预览。
          </p>
          <a
            v-if="downloadUrl"
            :href="downloadUrl"
            :download="artifact.path ?? artifact.title"
          >
            <n-button size="small" type="primary">下载文件</n-button>
          </a>
        </div>

        <!-- task_file 文本类但后端不可预览 -->
        <p
          v-else-if="isTaskFile"
          class="artifact-view__hint"
          data-testid="artifact-unavailable"
        >
          该文件类型不支持在线预览。
        </p>

        <p v-else class="artifact-view__hint" data-testid="artifact-unknown">
          未知来源的产物，不支持在线预览。
        </p>
      </component>
    </template>
  </section>
</template>

<style scoped>
.artifact-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  min-height: 0;
  padding: 16px;
  box-sizing: border-box;
  overflow: auto;
}
.artifact-view__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}
.artifact-view__title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.artifact-view__download {
  margin-left: auto;
  text-decoration: none;
}
.artifact-view__error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: 14px;
}
.artifact-view__hint {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: 14px;
  line-height: 1.6;
}
.artifact-view__binary {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
}
.artifact-view__body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.artifact-view__log-summary {
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
}
.artifact-view__json > summary {
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
}
.artifact-view__code {
  margin: 8px 0 0;
  padding: 12px;
  overflow: auto;
  border-radius: 6px;
  background: var(--n-code-color, #f6f7f9);
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
