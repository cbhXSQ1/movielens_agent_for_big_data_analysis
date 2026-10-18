<script setup lang="ts">
// 阶段 C · FC.5 →（动态化）：中栏容器。
// 标签集合 = 基础「对话」+ **已打开**的产物标签（openByConversation 决定）
//          + **资源标签**（模型 / 方案 / 文件，由 centerTabs store 决定）。
// 产物到达不自动开标签；对话里点产物超链接才开（CenterTabs 经 ChatView 提供的导航）；
// 产物标签可关闭（从已开集合移除，产物本身仍留在 store）。切换对话时标签随对话变化。
// 资源标签跨对话全局、按 key 去重，渲染组件经注册表 resourceViewFor(kind) 分发。
import { computed, watch, type Component } from 'vue'
import { storeToRefs } from 'pinia'
import ResultTabs from './ResultTabs.vue'
import ChatView from '@/views/tabs/ChatView.vue'
import ArtifactView from '@/views/tabs/ArtifactView.vue'
import { BASE_TABS, type ResultTabKey, type ShellTabDef } from './shellConfig'
import { resourceViewFor } from './centerTabViews'
import type { ShellTab } from './centerTabs'
import { useArtifactsStore } from '@/stores/artifacts'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { useConversationsStore } from '@/stores/conversations'
import type { ArtifactRef } from '@/api/types'

const active = defineModel<ResultTabKey>('active', { default: 'chat' })

const artifacts = useArtifactsStore()
const centerTabs = useCenterTabsStore()
const { currentId } = storeToRefs(useConversationsStore())
const { tabs: resourceTabs } = storeToRefs(centerTabs)

/** 基础「对话」+ 每个**已打开**的产物一个标签 + 每个资源标签。 */
const tabs = computed<ShellTabDef[]>(() => {
  const cid = currentId.value
  const byId = new Map(artifacts.list(cid).map((a) => [a.id, a]))
  const opened = artifacts
    .openIds(cid)
    .map((id) => byId.get(id))
    .filter((a): a is ArtifactRef => Boolean(a))
    .map((a) => ({ key: a.id, label: a.title || a.id, closable: true }))
  const resources: ShellTabDef[] = resourceTabs.value.map((t) => ({
    key: t.key,
    label: t.label,
    closable: true,
  }))
  return [...BASE_TABS, ...opened, ...resources]
})

function artifactOf(id: ResultTabKey) {
  return artifacts.find(currentId.value, id)
}

/** 某 key 对应的资源标签（非资源标签返回 null）。 */
function resourceTabOf(key: ResultTabKey): ShellTab | null {
  return resourceTabs.value.find((t) => t.key === key) ?? null
}

/** 某 key 的渲染组件（非资源标签 / 未知种类返回 null）。 */
function resourceViewOf(key: ResultTabKey): Component | null {
  const tab = resourceTabOf(key)
  return tab ? resourceViewFor(tab.kind) : null
}

/** 某 key 渲染组件的 props（非资源标签返回空对象）。 */
function resourcePayloadOf(key: ResultTabKey): Record<string, unknown> {
  return resourceTabOf(key)?.payload ?? {}
}

/** 关闭标签：资源标签走 centerTabs store，产物标签走 artifacts store；若关的是当前标签则回落「对话」。 */
function onCloseTab(key: ResultTabKey): void {
  if (resourceTabOf(key)) centerTabs.close(key)
  else artifacts.closeTab(currentId.value, key)
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
          <ArtifactView
            v-else-if="artifactOf(tab.key)"
            :artifact="artifactOf(tab.key)"
          />
          <component
            v-else-if="resourceViewOf(tab.key)"
            :is="resourceViewOf(tab.key)"
            v-bind="resourcePayloadOf(tab.key)"
          />
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
