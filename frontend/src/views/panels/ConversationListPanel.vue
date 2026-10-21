<script setup lang="ts">
// 阶段 D · FD.5：左栏①对话列表面板。
// 列出全部对话（conversations store），支持新建 / 切换；切换即由 store 关闭旧 SSE 流、
// 打开目标对话的流（见 conversations.open），中栏对话区随之更新。
// 列表加载按连接就绪触发（main.ts 已启动 /health 轮询），另提供手动刷新，避免对未就绪后端空转。
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NEmpty, NPopconfirm, NSpin, NTag } from 'naive-ui'
import { storeToRefs } from 'pinia'
import { useConnectionStore } from '@/stores/connection'
import { useConversationsStore } from '@/stores/conversations'

const conversations = useConversationsStore()
const connection = useConnectionStore()
const { conversations: list, currentId, loading, lastError } = storeToRefs(conversations)

const loaded = ref(false)

async function refresh(): Promise<void> {
  loaded.value = true
  await conversations.load()
}

onMounted(() => {
  if (list.value.length === 0 && connection.connected) void refresh()
})

watch(
  () => connection.connected,
  (ready) => {
    if (ready && !loaded.value && list.value.length === 0) void refresh()
  },
)

async function createNew(): Promise<void> {
  await conversations.createAndOpen()
}

function selectConversation(id: string): void {
  if (id !== currentId.value) conversations.switchTo(id)
}

/** 确认删除：后端删除成功后列表移除、清产物缓存与标签；当前对话被删则关闭回空白态。 */
async function removeConversation(id: string): Promise<void> {
  await conversations.remove(id)
}

function formatTime(ts: number): string {
  if (!ts) return ''
  const date = new Date(ts * 1000)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const isEmpty = computed(() => !loading.value && list.value.length === 0)
</script>

<template>
  <div class="conversation-list" data-testid="panel-list">
    <div class="conversation-list__head">
      <n-button
        type="primary"
        size="small"
        block
        data-testid="conversation-new"
        @click="createNew"
      >
        新建对话
      </n-button>
    </div>

    <div class="conversation-list__body">
      <n-spin :show="loading">
        <n-empty
          v-if="isEmpty"
          description="暂无对话"
          data-testid="conversation-empty"
        />
        <ul v-else class="conversation-list__items">
          <li
            v-for="conv in list"
            :key="conv.conversation_id"
            class="conversation-list__row"
          >
            <button
              type="button"
              class="conversation-item"
              :class="{ 'conversation-item--active': conv.conversation_id === currentId }"
              :data-testid="`conversation-item-${conv.conversation_id}`"
              @click="selectConversation(conv.conversation_id)"
            >
              <span class="conversation-item__title">
                {{ conv.title || '未命名对话' }}
              </span>
              <span class="conversation-item__meta">
                <n-tag
                  v-if="conv.conversation_id === currentId"
                  size="small"
                  type="primary"
                  :bordered="false"
                >
                  当前
                </n-tag>
                <span class="conversation-item__time">{{ formatTime(conv.updated_at) }}</span>
              </span>
            </button>
            <n-popconfirm
              positive-text="删除"
              negative-text="取消"
              @positive-click="removeConversation(conv.conversation_id)"
            >
              <template #trigger>
                <n-button
                  class="conversation-item__delete"
                  size="tiny"
                  quaternary
                  type="error"
                  aria-label="删除对话"
                  :data-testid="`conversation-delete-${conv.conversation_id}`"
                >
                  删除
                </n-button>
              </template>
              确定删除「{{ conv.title || '未命名对话' }}」？此操作不可撤销。
            </n-popconfirm>
          </li>
        </ul>
      </n-spin>
    </div>

    <p v-if="lastError" class="conversation-list__error" data-testid="conversation-error">
      {{ lastError }}
    </p>

    <div class="conversation-list__foot">
      <n-button size="tiny" quaternary data-testid="conversation-refresh" @click="refresh">
        刷新
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.conversation-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.conversation-list__head {
  padding: 16px;
  border-bottom: 1px solid var(--n-border-color, #e6e9f0);
}
.conversation-list__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 8px;
}
.conversation-list__items {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.conversation-list__row {
  position: relative;
}
/* 删除入口：默认隐去，悬停该行或聚焦时显现（不占位、不推开标题）。 */
.conversation-item__delete {
  position: absolute;
  top: 6px;
  right: 8px;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.conversation-list__row:hover .conversation-item__delete,
.conversation-item__delete:focus-visible {
  opacity: 1;
}
.conversation-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.conversation-item:hover {
  background: var(--n-action-color, rgba(0, 0, 0, 0.04));
}
.conversation-item--active {
  background: var(--n-primary-color-opacity, rgba(47, 111, 237, 0.1));
}
.conversation-item__title {
  overflow: hidden;
  color: var(--n-text-color-1, #1f2733);
  font-size: var(--ml-text-md, 15px);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conversation-item__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.conversation-item__time {
  color: var(--n-text-color-3, #8a94a6);
  font-size: var(--ml-text-xs, 12px);
}
.conversation-list__error {
  margin: 0;
  padding: 6px 16px;
  color: var(--n-error-color, #d03050);
  font-size: var(--ml-text-sm, 13px);
}
.conversation-list__foot {
  padding: 8px 16px;
  border-top: 1px solid var(--n-border-color, #e6e9f0);
}
</style>
