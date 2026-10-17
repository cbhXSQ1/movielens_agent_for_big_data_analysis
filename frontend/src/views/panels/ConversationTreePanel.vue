<script setup lang="ts">
// 阶段 F（fe-tree）· 左栏②对话树入口。
// 切到②**不自动**弹出浮窗：先在左栏给一张**缩略图预览**；点「放大查看」才打开悬浮窗
// （大画布 + 横/纵滑块平移 + 悬停缩略 + 「切到这里」）。
// 面板负责读树（经 tree store）；浮窗复用同一份 store 数据。
import { computed, onMounted, ref, watch } from 'vue'
import { NButton } from 'naive-ui'
import ConversationTreeWindow from '@/components/tree/ConversationTreeWindow.vue'
import TreeThumbnail from '@/components/tree/TreeThumbnail.vue'
import { useConversationsStore } from '@/stores/conversations'
import { useTreeStore } from '@/stores/tree'

const open = ref(false)
const conversations = useConversationsStore()
const tree = useTreeStore()

const cid = computed(() => conversations.currentId)

async function refresh(): Promise<void> {
  const id = cid.value
  if (!id) {
    tree.reset()
    return
  }
  await tree.load(id)
}

onMounted(() => {
  void refresh()
})

watch(cid, () => {
  void refresh()
})
</script>

<template>
  <div class="conversation-tree-panel" data-testid="panel-tree">
    <p class="conversation-tree-panel__hint">
      下方是当前对话的缩略预览；点「放大查看」打开浮窗看大图（横/纵滑块平移、悬停缩略该次提问、「切到这里」）。
    </p>

    <p v-if="!cid" class="conversation-tree-panel__empty">
      请先在①对话列表中选择一个对话
    </p>

    <template v-else>
      <TreeThumbnail
        :nodes="tree.nodes"
        :current-node-id="tree.currentNodeId"
        @open="open = true"
      />
      <n-button
        type="primary"
        size="medium"
        block
        data-testid="tree-open"
        @click="open = true"
      >
        放大查看对话树
      </n-button>
    </template>

    <ConversationTreeWindow v-model:show="open" />
  </div>
</template>

<style scoped>
.conversation-tree-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
}
.conversation-tree-panel__hint {
  margin: 0;
  color: var(--n-text-color-2, #4b5563);
  font-size: var(--ml-text-sm, 15px);
  line-height: var(--ml-leading, 1.7);
}
.conversation-tree-panel__empty {
  margin: 0;
  color: var(--n-text-color-3, #6b7280);
  font-size: var(--ml-text-sm, 15px);
}
</style>
