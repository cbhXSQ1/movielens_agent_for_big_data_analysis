<script setup lang="ts">
// 顶栏连接状态胶囊（阶段 C · 迭代打磨）。
// 语义清楚：图标 + 文案（已连接 / 后端未连接 / 连接中）；悬停出说明（怎么启动后端、
// 可用工具数、模型是否可用）；整颗可点，点击立即重试探活（connection.check）。
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { NTooltip } from 'naive-ui'
import { useConnectionStore } from '@/stores/connection'
import ShellIcon from './ShellIcon.vue'

const connection = useConnectionStore()
const { connected, checking, lastError, toolCount, llmSupported, llmConfigured } =
  storeToRefs(connection)

type State = 'online' | 'checking' | 'offline'

const state = computed<State>(() => {
  if (connected.value) return 'online'
  if (checking.value) return 'checking'
  return 'offline'
})

const statusText = computed(() => {
  if (state.value === 'online') return '已连接'
  if (state.value === 'checking') return '连接中…'
  return '后端未连接'
})

const detail = computed(() => {
  if (state.value === 'online') {
    const tools = `可用工具 ${toolCount.value} 个`
    const model = llmSupported.value
      ? `大模型${llmConfigured.value ? '已配置' : '未配置'}`
      : '大模型未启用'
    return `后端在线 · ${tools} · ${model}`
  }
  if (state.value === 'checking') return '正在检测后端连接…'
  const reason = lastError.value ? `${lastError.value}。` : ''
  return `${reason}后端未连接（start_backend.bat，默认 http://localhost:8765）`
})

async function retry(): Promise<void> {
  await connection.check()
}
</script>

<template>
  <n-tooltip trigger="hover" :delay="200">
    <template #trigger>
      <button
        type="button"
        class="conn-capsule"
        :class="`conn-capsule--${state}`"
        :title="detail"
        :aria-label="`连接状态：${statusText}。${detail}`"
        :aria-busy="state === 'checking'"
        data-testid="connection-status"
        @click="retry"
      >
        <span class="conn-capsule__dot" data-testid="connection-dot" aria-hidden="true">
          <ShellIcon :name="state === 'checking' ? 'refresh' : 'status'" :size="10" />
        </span>
        <span class="conn-capsule__text">{{ statusText }}</span>
        <span class="conn-capsule__retry" data-testid="connection-retry" aria-hidden="true">
          <ShellIcon name="refresh" :size="12" />
        </span>
      </button>
    </template>
    {{ detail }}
  </n-tooltip>
</template>

<style scoped>
.conn-capsule {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--n-border-color, #e6e9f0);
  border-radius: 999px;
  background: transparent;
  color: var(--n-text-color-2, #4b5563);
  font-family: inherit;
  font-size: var(--ml-text-sm, 13px);
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s, color 0.15s;
}
.conn-capsule:hover {
  background: var(--n-action-color, rgba(0, 0, 0, 0.04));
}
.conn-capsule__dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.conn-capsule__text {
  white-space: nowrap;
}
.conn-capsule__retry {
  display: inline-flex;
  align-items: center;
  opacity: 0;
  transition: opacity 0.15s;
}
.conn-capsule:hover .conn-capsule__retry {
  opacity: 0.7;
}

.conn-capsule--online {
  border-color: rgba(24, 160, 88, 0.28);
  background: rgba(24, 160, 88, 0.08);
  color: #128d4b;
}
.conn-capsule--online .conn-capsule__dot {
  color: #18a058;
}
.conn-capsule--checking {
  border-color: rgba(240, 160, 32, 0.3);
  background: rgba(240, 160, 32, 0.1);
  color: #b87503;
}
.conn-capsule--checking .conn-capsule__dot {
  color: #f0a020;
  animation: conn-spin 1.1s linear infinite;
}
.conn-capsule--offline {
  border-color: rgba(208, 48, 80, 0.28);
  background: rgba(208, 48, 80, 0.08);
  color: #c02a49;
}
.conn-capsule--offline .conn-capsule__dot {
  color: #d03050;
}
@keyframes conn-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
