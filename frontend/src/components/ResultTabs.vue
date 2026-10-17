<script setup lang="ts">
// 阶段 C · FC.5 →（动态化）：中栏结果视图标签栏本体。
// 标签集合由外部（CenterTabs）按 artifacts store 计算后以 `tabs` 传入；
// 每个标签内容经默认作用域插槽 `#default="{ tab }"` 注入。
// 产物标签（`tab.closable`）带关闭按钮（×），点击 emit `close`（由上层移除标签，
// 不删产物本身）；基础「对话」标签不可关。
import { NTabPane, NTabs } from 'naive-ui'
import type { ResultTabKey, ShellTabDef } from './shellConfig'

defineProps<{ tabs: readonly ShellTabDef[] }>()

const active = defineModel<ResultTabKey>('active', { default: 'chat' })

const emit = defineEmits<{
  (e: 'close', key: ResultTabKey): void
}>()
</script>

<template>
  <n-tabs
    v-model:value="active"
    type="line"
    class="result-tabs"
    data-testid="result-tabs"
  >
    <n-tab-pane
      v-for="tab in tabs"
      :key="tab.key"
      :name="tab.key"
    >
      <template #tab>
        <span class="result-tabs__label">
          <span class="result-tabs__label-text">{{ tab.label }}</span>
          <button
            v-if="tab.closable"
            type="button"
            class="result-tabs__close"
            :aria-label="`关闭 ${tab.label}`"
            :data-testid="`tab-close-${tab.key}`"
            @click.stop="emit('close', tab.key)"
          >
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              aria-hidden="true"
            >
              <path d="M6 6l12 12" />
              <path d="M18 6L6 18" />
            </svg>
          </button>
        </span>
      </template>
      <slot :tab="tab" />
    </n-tab-pane>
  </n-tabs>
</template>

<style scoped>
.result-tabs {
  height: 100%;
}
.result-tabs :deep(.n-tabs-nav) {
  padding: 0 16px;
}
.result-tabs :deep(.n-tabs-tab) {
  font-size: var(--ml-text-lg, 19px);
}
.result-tabs__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.result-tabs__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--n-text-color-3, #6b7280);
  cursor: pointer;
  line-height: 0;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.result-tabs__close:hover {
  background: var(--n-border-color, #e5e7eb);
  color: var(--n-text-color-1, #111827);
}
</style>
