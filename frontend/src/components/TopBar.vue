<script setup lang="ts">
// 阶段 C · FC.1（迭代打磨）：顶栏。产品名 + 连接状态（ConnectionStatus）+ 设置入口。
import { NButton } from 'naive-ui'
import ConnectionStatus from './ConnectionStatus.vue'
import ShellIcon from './ShellIcon.vue'

defineEmits<{ (e: 'open-settings'): void }>()
</script>

<template>
  <header class="top-bar" data-testid="top-bar">
    <div class="top-bar__brand">
      <span class="top-bar__mark" aria-hidden="true">ML</span>
      <div class="top-bar__titles">
        <h1 class="top-bar__title">电影数据分析助手</h1>
        <p class="top-bar__subtitle">MovieLens · 大数据分析 Agent</p>
      </div>
    </div>

    <div class="top-bar__actions">
      <ConnectionStatus />

      <n-button
        size="small"
        secondary
        type="primary"
        data-testid="settings-entry"
        @click="$emit('open-settings')"
      >
        <template #icon>
          <ShellIcon name="settings" :size="16" />
        </template>
        设置
      </n-button>
    </div>
  </header>
</template>

<style scoped>
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: 72px;
  padding: 0 22px;
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
  background: var(--n-color, #ffffff);
}
.top-bar__brand {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}
.top-bar__mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: var(--n-primary-color, #2f6fed);
  color: #fff;
  font-size: var(--ml-text-md, 16px);
  font-weight: 700;
  letter-spacing: 0.6px;
}
.top-bar__titles {
  min-width: 0;
}
.top-bar__title {
  margin: 0;
  color: var(--n-text-color-1, #111827);
  font-size: 22px;
  font-weight: 600;
  line-height: 1.3;
}
.top-bar__subtitle {
  margin: 3px 0 0;
  color: var(--n-text-color-3, #6b7280);
  font-size: var(--ml-text-sm, 14px);
  line-height: 1.35;
}
.top-bar__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>
