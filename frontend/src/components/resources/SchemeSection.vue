<script setup lang="ts">
// FG.4 · 方案文件段：列出方案 + 上传 .json（multipart，字段 file + 可选 name）+ 查看完整内容。
// 经 useResourcesStore（§3.11.3）：上传成功后 store 就地重列方案；校验结果随上传响应回显。
// 内容查看走 getSchemeContent（§3.11.3，文件不存在 → FILE_NOT_FOUND）。
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NInput, NSpin, NTag } from 'naive-ui'
import { useResourcesStore } from '@/stores/resources'
import type { SchemeInfo, ValidationResult } from '@/api/types'
import { describeError } from './resourceUtils'

const resources = useResourcesStore()
const { schemes, loading } = storeToRefs(resources)

const inputEl = ref<HTMLInputElement | null>(null)
const name = ref('')
const uploading = ref(false)
const uploadError = ref('')
const savedName = ref('')
const validation = ref<ValidationResult | null>(null)

const schemeLoading = computed(() => loading.value.schemes)
const isEmpty = computed(
  () => !schemeLoading.value && schemes.value.length === 0,
)

interface ContentState {
  name: string
  loading: boolean
  error: string
  json: string
}
const content = ref<ContentState | null>(null)

function schemeName(scheme: SchemeInfo): string {
  return String(scheme.name ?? scheme.path ?? '')
}

async function viewScheme(scheme: SchemeInfo): Promise<void> {
  const target = schemeName(scheme)
  if (!target) return
  content.value = { name: target, loading: true, error: '', json: '' }
  try {
    const res = await resources.loadSchemeContent(target)
    content.value = {
      name: res.name || target,
      loading: false,
      error: '',
      json: JSON.stringify(res.content ?? null, null, 2),
    }
  } catch (err) {
    content.value = {
      name: target,
      loading: false,
      error: describeError(err),
      json: '',
    }
  }
}

function pick(): void {
  uploadError.value = ''
  savedName.value = ''
  validation.value = null
  inputEl.value?.click()
}

async function onPick(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files && input.files[0] ? input.files[0] : null
  if (!file) return
  uploading.value = true
  uploadError.value = ''
  validation.value = null
  try {
    const res = await resources.uploadScheme(
      file,
      name.value.trim() || undefined,
    )
    savedName.value = res.saved?.name ?? file.name
    validation.value = res.validation ?? null
    name.value = ''
  } catch (err) {
    uploadError.value = describeError(err)
  } finally {
    uploading.value = false
    try {
      input.value = ''
    } catch {
      /* 忽略 */
    }
  }
}

function refresh(): void {
  void resources.loadSchemes()
}

const validationOk = computed(() => validation.value?.ok === true)
</script>

<template>
  <section class="resource-section" data-testid="resources-segment-schemes">
    <header class="resource-section__head">
      <h3 class="resource-section__title">方案文件</h3>
      <n-button size="tiny" quaternary data-testid="schemes-refresh" @click="refresh">
        刷新
      </n-button>
    </header>

    <n-spin :show="schemeLoading">
      <n-empty
        v-if="isEmpty"
        description="暂无方案文件，可上传 .json"
        data-testid="schemes-empty"
      />
      <ul v-else class="resource-list" data-testid="schemes-list">
        <li
          v-for="(scheme, index) in schemes"
          :key="schemeName(scheme) || index"
          class="resource-card resource-card--row"
          :data-testid="`scheme-item-${index}`"
        >
          <span class="resource-card__name">{{ schemeName(scheme) || '（未命名方案）' }}</span>
          <n-button
            size="tiny"
            secondary
            :disabled="!schemeName(scheme)"
            :data-testid="`scheme-view-${index}`"
            @click="viewScheme(scheme)"
          >
            查看
          </n-button>
        </li>
      </ul>
    </n-spin>

    <p v-if="resources.errors.schemes" class="resource-error" data-testid="schemes-error">
      {{ resources.errors.schemes }}
    </p>

    <div class="resource-upload">
      <div data-testid="scheme-upload-name">
        <n-input
          v-model:value="name"
          size="small"
          clearable
          placeholder="方案名（可选）"
        />
      </div>
      <input
        ref="inputEl"
        type="file"
        accept=".json"
        class="resource-upload__input"
        data-testid="scheme-upload-input"
        @change="onPick"
      />
      <n-button
        size="small"
        type="primary"
        :loading="uploading"
        data-testid="scheme-upload-btn"
        @click="pick"
      >
        上传 .json
      </n-button>
    </div>

    <p v-if="uploadError" class="resource-error" data-testid="scheme-upload-error">
      {{ uploadError }}
    </p>

    <div v-if="validation" class="resource-block" data-testid="scheme-validation">
      <div class="resource-block__head">
        <span>已保存：{{ savedName || '—' }}</span>
        <n-tag
          size="small"
          :type="validationOk ? 'success' : 'error'"
          :bordered="false"
        >
          {{ validationOk ? '校验通过' : '校验未通过' }}
        </n-tag>
      </div>
      <pre class="resource-block__code">{{ JSON.stringify(validation, null, 2) }}</pre>
    </div>

    <div v-if="content" class="resource-block" data-testid="scheme-content">
      <div class="resource-block__head">
        <span>{{ content.name }}</span>
      </div>
      <p v-if="content.loading" class="resource-section__hint">加载中…</p>
      <p v-else-if="content.error" class="resource-error">{{ content.error }}</p>
      <pre v-else class="resource-block__code">{{ content.json }}</pre>
    </div>
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
  font-size: 13px;
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
  justify-content: space-between;
  gap: 8px;
}
.resource-card__name {
  overflow: hidden;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-upload {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.resource-upload__input {
  display: none;
}
.resource-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  border-radius: 8px;
}
.resource-block__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}
.resource-block__code {
  max-height: 260px;
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
