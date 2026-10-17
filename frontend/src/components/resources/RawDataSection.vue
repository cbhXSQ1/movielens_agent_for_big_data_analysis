<script setup lang="ts">
// FG.2 · 原始数据段：列表（数据集/文件、大小、时间）+ 上传（.dat/.zip，multipart）。
// 经 useResourcesStore（§3.11.1）：上传成功由 store 用响应 datasets 就地刷新。
// 类型不符（UNSUPPORTED_FILE）/ 过大（UPLOAD_TOO_LARGE）由后端错误码转为中文提示。
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NInput, NSpin, NTag } from 'naive-ui'
import { useResourcesStore } from '@/stores/resources'
import { formatBytes } from '@/components/results/format'
import { describeError, formatTimestamp } from './resourceUtils'

const resources = useResourcesStore()
const { rawDatasets, rawDir, loading } = storeToRefs(resources)

const inputEl = ref<HTMLInputElement | null>(null)
const name = ref('')
const uploading = ref(false)
const uploadError = ref('')
const uploadOk = ref(false)

const rawLoading = computed(() => loading.value.raw)
const isEmpty = computed(() => !rawLoading.value && rawDatasets.value.length === 0)

function pick(): void {
  uploadError.value = ''
  uploadOk.value = false
  inputEl.value?.click()
}

async function onPick(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const files = input.files ? Array.from(input.files) : []
  if (files.length === 0) return
  uploading.value = true
  uploadError.value = ''
  uploadOk.value = false
  try {
    await resources.uploadRaw(files, name.value.trim() || undefined)
    uploadOk.value = true
    name.value = ''
  } catch (err) {
    uploadError.value = describeError(err)
  } finally {
    uploading.value = false
    try {
      input.value = ''
    } catch {
      /* 某些环境不接受重置文件输入，忽略 */
    }
  }
}

function refresh(): void {
  void resources.loadRaw()
}
</script>

<template>
  <section class="resource-section" data-testid="resources-segment-raw">
    <header class="resource-section__head">
      <h3 class="resource-section__title">原始数据</h3>
      <n-button size="tiny" quaternary data-testid="raw-refresh" @click="refresh">
        刷新
      </n-button>
    </header>
    <p v-if="rawDir" class="resource-section__dir" data-testid="raw-dir">{{ rawDir }}</p>

    <n-spin :show="rawLoading">
      <n-empty
        v-if="isEmpty"
        description="暂无原始数据，可上传 .dat 或 .zip"
        data-testid="raw-empty"
      />
      <ul v-else class="resource-list" data-testid="raw-list">
        <li
          v-for="dataset in rawDatasets"
          :key="dataset.name"
          class="resource-card"
          :data-testid="`raw-dataset-${dataset.name}`"
        >
          <div class="resource-card__row">
            <span class="resource-card__name">{{ dataset.name }}</span>
            <n-tag size="small" :bordered="false">{{ formatBytes(dataset.size) }}</n-tag>
          </div>
          <div class="resource-card__meta">
            <span>{{ formatTimestamp(dataset.updated_at) }}</span>
          </div>
          <ul v-if="dataset.files.length" class="resource-card__files">
            <li v-for="file in dataset.files" :key="file.name">
              <span class="resource-card__file">{{ file.name }}</span>
              <span class="resource-card__size">{{ formatBytes(file.size) }}</span>
            </li>
          </ul>
        </li>
      </ul>
    </n-spin>

    <p v-if="resources.errors.raw" class="resource-error" data-testid="raw-error">
      {{ resources.errors.raw }}
    </p>

    <div class="resource-upload">
      <div data-testid="raw-upload-name">
        <n-input
          v-model:value="name"
          size="small"
          clearable
          placeholder="数据集名（可选）"
        />
      </div>
      <input
        ref="inputEl"
        type="file"
        multiple
        accept=".dat,.zip"
        class="resource-upload__input"
        data-testid="raw-upload-input"
        @change="onPick"
      />
      <n-button
        size="small"
        type="primary"
        :loading="uploading"
        data-testid="raw-upload-btn"
        @click="pick"
      >
        上传 .dat/.zip
      </n-button>
    </div>

    <p v-if="uploadError" class="resource-error" data-testid="raw-upload-error">
      {{ uploadError }}
    </p>
    <p v-else-if="uploadOk" class="resource-ok" data-testid="raw-upload-ok">
      上传成功，列表已刷新。
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
.resource-section__dir {
  margin: 0;
  overflow: hidden;
  color: var(--n-text-color-3, #999);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__meta {
  color: var(--n-text-color-3, #999);
  font-size: 12px;
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
  font-size: 13px;
}
.resource-card__file {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__size {
  color: var(--n-text-color-3, #999);
}
.resource-upload {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.resource-upload__input {
  display: none;
}
.resource-error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: 13px;
}
.resource-ok {
  margin: 0;
  color: var(--n-success-color, #18a058);
  font-size: 13px;
}
</style>
