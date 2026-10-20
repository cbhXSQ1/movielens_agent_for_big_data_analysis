<script setup lang="ts">
// FG.4 · 方案文件段：列出方案 + 上传 .json（multipart，字段 file + 可选 name）+ 「查看」中栏开标签。
// 经 useResourcesStore（§3.11.3）：上传经 Agent 转发到远端能力服务落盘，成功后 store 就地重列方案；
// 校验结果随上传响应回显；若远端回执 overwritten=true 则提示已覆盖同名方案。
// 「查看」把方案登记为中栏资源标签（内容由中栏 SchemeContentView 走 getSchemeContent（§3.11.3，文件不存在 → FILE_NOT_FOUND）拉取）。
// 删除：仅自定义方案可删；两份默认方案是交付物，永不提供删除入口（与远端护栏一致）。
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { NButton, NEmpty, NInput, NPopconfirm, NSpin, NTag } from 'naive-ui'
import { useResourcesStore } from '@/stores/resources'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { schemeTab } from '@/components/centerTabViews'
import { isApiError } from '@/api/http'
import type { SchemeInfo, ValidationResult } from '@/api/types'
import { describeError } from './resourceUtils'

const resources = useResourcesStore()
const centerTabs = useCenterTabsStore()
const { schemes, loading } = storeToRefs(resources)

const inputEl = ref<HTMLInputElement | null>(null)
const name = ref('')
const uploading = ref(false)
const uploadError = ref('')
const savedName = ref('')
const validation = ref<ValidationResult | null>(null)
// 远端回执 overwritten=true：本次上传替换了同名方案。
const overwritten = ref(false)

const schemeLoading = computed(() => loading.value.schemes)
const isEmpty = computed(
  () => !schemeLoading.value && schemes.value.length === 0,
)

function schemeName(scheme: SchemeInfo): string {
  return String(scheme.name ?? scheme.path ?? '')
}

/** 删除接口只接受单层文件名：从 `name` / `path` 取最后一段（列表常只给 `config/x.json`）。 */
function schemeFile(scheme: SchemeInfo): string {
  const raw = schemeName(scheme)
  const cut = Math.max(raw.lastIndexOf('/'), raw.lastIndexOf('\\'))
  return cut >= 0 ? raw.slice(cut + 1) : raw
}

// 随仓库交付的两份默认方案：远端无条件拒绝删除，这里直接不显示入口。
const DEFAULT_SCHEMES = new Set([
  'cleaning_rules.v1.json',
  'scoring_scheme.v1.json',
])

function isDefaultScheme(scheme: SchemeInfo): boolean {
  return DEFAULT_SCHEMES.has(schemeFile(scheme))
}

const deleting = ref('')
const deleteError = ref('')

/** 删除失败的中文提示：占用中单独说清楚「等它结束」，其余走统一码表。 */
function deleteErrorText(err: unknown): string {
  if (isApiError(err) && err.code === 'TASK_ALREADY_RUNNING') {
    return '有任务在运行，等它结束再删'
  }
  return describeError(err)
}

async function removeScheme(scheme: SchemeInfo): Promise<void> {
  const target = schemeFile(scheme)
  if (!target || isDefaultScheme(scheme)) return
  deleting.value = target
  deleteError.value = ''
  try {
    await resources.deleteScheme(target)
  } catch (err) {
    deleteError.value = deleteErrorText(err)
  } finally {
    deleting.value = ''
  }
}

/** 「查看」：把方案作为中栏资源标签打开（内容由中栏 SchemeContentView 拉取）。 */
function openScheme(scheme: SchemeInfo): void {
  const target = schemeName(scheme)
  if (!target) return
  centerTabs.open(schemeTab(target))
}

function pick(): void {
  uploadError.value = ''
  savedName.value = ''
  validation.value = null
  overwritten.value = false
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
    overwritten.value = res.overwritten === true
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
          <div class="resource-card__actions">
            <n-button
              size="tiny"
              secondary
              :disabled="!schemeName(scheme)"
              :data-testid="`scheme-view-${index}`"
              @click="openScheme(scheme)"
            >
              查看
            </n-button>
            <n-popconfirm
              v-if="!isDefaultScheme(scheme) && schemeFile(scheme)"
              positive-text="删除"
              negative-text="取消"
              @positive-click="removeScheme(scheme)"
            >
              <template #trigger>
                <n-button
                  size="tiny"
                  quaternary
                  type="error"
                  :loading="deleting === schemeFile(scheme)"
                  :data-testid="`scheme-delete-${index}`"
                >
                  删除
                </n-button>
              </template>
              确定删除「{{ schemeFile(scheme) }}」？此操作不可撤销。
            </n-popconfirm>
          </div>
        </li>
      </ul>
    </n-spin>

    <p v-if="resources.errors.schemes" class="resource-error" data-testid="schemes-error">
      {{ resources.errors.schemes }}
    </p>

    <p v-if="deleteError" class="resource-error" data-testid="scheme-delete-error">
      {{ deleteError }}
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

    <p
      v-if="overwritten"
      class="resource-section__hint"
      data-testid="scheme-overwritten-note"
    >
      已覆盖同名方案：原有内容已被替换。
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
.resource-section__hint {
  margin: 0;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-md);
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
  font-size: var(--fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-card__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
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
  font-size: var(--fs-md);
}
.resource-block__code {
  max-height: 260px;
  margin: 0;
  padding: 8px 10px;
  overflow: auto;
  border-radius: 6px;
  background: var(--n-code-color, #f6f7f9);
  font-size: var(--fs-sm);
  white-space: pre-wrap;
  word-break: break-all;
}
.resource-error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: var(--fs-md);
}
</style>
