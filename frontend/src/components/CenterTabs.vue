<script setup lang="ts">
// 阶段 C · FC.5 →（动态化）：中栏容器。
// 标签集合 = 基础「对话」+ **已打开**的产物标签（openByConversation 决定）。
// 产物到达不自动开标签；对话里点产物超链接才开（CenterTabs 经 ChatView 提供的导航）；
// 产物标签可关闭（从已开集合移除，产物本身仍留在 store）。切换对话时标签随对话变化。
import { computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import ResultTabs from './ResultTabs.vue'
import ChatView from '@/views/tabs/ChatView.vue'
import ArtifactView from '@/views/tabs/ArtifactView.vue'
import { BASE_TABS, type ResultTabKey, type ShellTabDef } from './shellConfig'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConversationsStore } from '@/stores/conversations'
import type { ArtifactRef } from '@/api/types'

const active = defineModel<ResultTabKey>('active', { default: 'chat' })

const artifacts = useArtifactsStore()
const { currentId } = storeToRefs(useConversationsStore())

/** 基础「对话」+ 每个**已打开**的产物一个标签（标签名用 artifact.title）。 */
const tabs = computed<ShellTabDef[]>(() => {
  const cid = currentId.value
  const byId = new Map(artifacts.list(cid).map((a) => [a.id, a]))
  const opened = artifacts
    .openIds(cid)
    .map((id) => byId.get(id))
    .filter((a): a is ArtifactRef => Boolean(a))
    .map((a) => ({ key: a.id, label: a.title || a.id, closable: true }))
  return [...BASE_TABS, ...opened]
})

function artifactOf(id: ResultTabKey) {
  return artifacts.find(currentId.value, id)
}

/** 关闭产物标签：从已开集合移除；若关的是当前标签则回落「对话」。 */
function onCloseTab(key: ResultTabKey): void {
  artifacts.closeTab(currentId.value, key)
  if (active.value === key) active.value = 'chat'
}

// 切换对话：标签随该对话的已开集合变化，回到基础「对话」标签。
watch(currentId, () => {
  active.value = 'chat'
})

// 当前标签因关闭 / 切换对话而失效时，回落到「对话」。
watch(tabs, (list) => {
  if (!list.some((t) => t.key === active.value)) active.value = 'chat'
})
</script>

<template>
  <section class="center-tabs" data-testid="center-tabs">
    <ResultTabs v-model:active="active" :tabs="tabs" @close="onCloseTab">
      <template #default="{ tab }">
        <slot :name="tab.key" :tab="tab">
          <ChatView v-if="tab.key === 'chat'" />
          <ArtifactView v-else :artifact="artifactOf(tab.key)" />
        </slot>
      </template>
    </ResultTabs>
  </section>
</template>

<style scoped>
.center-tabs {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  background: var(--n-color, #ffffff);
}
.center-tabs :deep(.n-tabs) {
  flex: 1 1 auto;
  min-height: 0;
}
.center-tabs :deep(.n-tabs-pane-wrapper) {
  height: 100%;
}
.center-tabs :deep(.n-tab-pane) {
  height: 100%;
  overflow: auto;
}
</style>
