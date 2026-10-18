<script setup lang="ts">
// 阶段 C · 中栏「方案内容」标签（资源标签 kind='scheme'）。
// 经 resources store 取方案完整内容（组件不直接 fetch），整栏铺满中栏，美化 JSON 呈现
// （与左栏「方案文件」段的内联查看同观感）。
import { computed, ref, watch } from 'vue'
import { NSpin } from 'naive-ui'
import { useResourcesStore } from '@/stores/resources'

const props = defineProps<{ name: string }>()

const resources = useResourcesStore()

const loading = ref(false)
const error = ref('')
const content = ref<unknown>(null)
const loaded = ref(false)

const json = computed(() =>
  loaded.value ? JSON.stringify(content.value ?? null, null, 2) : '',
)

watch(
  () => props.name,
  async (name) => {
    loading.value = false
    error.value = ''
    content.value = null
    loaded.value = false
    if (!name) {
      error.value = '未给出方案名，无法取内容'
      return
    }
    loading.value = true
    try {
      const res = await resources.loadSchemeContent(name)
      content.value = res.content
      loaded.value = true
    } catch (err) {
      error.value = err instanceof Error ? err.message : '方案内容拉取失败'
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <section class="scheme-content-view" data-testid="scheme-content-view">
    <header class="scheme-content-view__head">
      <h3 class="scheme-content-view__title" data-testid="scheme-content-title">
        {{ name }}
      </h3>
    </header>

    <n-spin v-if="loading" data-testid="scheme-content-loading" />

    <p
      v-else-if="error"
      class="scheme-content-view__error"
      data-testid="scheme-content-error"
    >
      {{ error }}
    </p>

    <pre
      v-else-if="loaded"
      class="scheme-content-view__code"
      data-testid="scheme-content-json"
    >{{ json }}</pre>
  </section>
</template>

<style scoped>
.scheme-content-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  padding: 16px;
  box-sizing: border-box;
  overflow: auto;
}
.scheme-content-view__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.scheme-content-view__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.scheme-content-view__code {
  margin: 0;
  padding: 12px;
  overflow: auto;
  border-radius: 8px;
  background: var(--n-code-color, #f6f7f9);
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-all;
}
.scheme-content-view__error {
  margin: 0;
  color: var(--n-error-color, #d03050);
  font-size: 14px;
}
</style>
