<script setup lang="ts">
// 阶段 C · 外壳根组件：顶栏 + （左栏｜中栏）。
// 状态：activePanel / collapsed / drawerOpen / activeTab + 设置弹窗。
// 持久化：activePanel / activeTab（经 shellConfig 的键）与「当前对话」（conversations store）
// 存 localStorage；启动时恢复 —— 使**刷新不丢当前状态、不退出当前对话**。
import { onMounted, ref, watch, type Component } from 'vue'
import TopBar from '@/components/TopBar.vue'
import LeftRail from '@/components/LeftRail.vue'
import CenterTabs from '@/components/CenterTabs.vue'
import SettingsModal from '@/components/SettingsModal.vue'
import ConversationListPanel from '@/views/panels/ConversationListPanel.vue'
import ConversationTreePanel from '@/views/panels/ConversationTreePanel.vue'
import ResourcesPanel from '@/views/panels/ResourcesPanel.vue'
import TasksPanel from '@/views/panels/TasksPanel.vue'
import { useNarrowScreen } from '@/composables/useNarrowScreen'
import { provideResultTabNav } from '@/composables/useResultTabNav'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConversationsStore } from '@/stores/conversations'
import {
  SHELL_STORAGE_KEYS,
  LEFT_RAIL_WIDTH,
  type PanelKey,
  type ResultTabKey,
} from '@/components/shellConfig'

/** 左栏面板 key → 组件（后续阶段替换对应 view 文件即可，无需改壳层）。 */
const PANEL_VIEWS: Record<PanelKey, Component> = {
  list: ConversationListPanel,
  tree: ConversationTreePanel,
  resources: ResourcesPanel,
  progress: TasksPanel,
}

const PANELS: PanelKey[] = ['list', 'tree', 'resources', 'progress']

const conversations = useConversationsStore()
const artifacts = useArtifactsStore()

function readStorage(key: string): string | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string): void {
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(key, value)
  } catch {
    /* 存储不可用时静默忽略 */
  }
}

function readCollapsed(): boolean {
  return readStorage(SHELL_STORAGE_KEYS.collapsed) === '1'
}

function readPanel(): PanelKey {
  const v = readStorage(SHELL_STORAGE_KEYS.panel)
  return v && (PANELS as string[]).includes(v) ? (v as PanelKey) : 'list'
}

function readTab(): ResultTabKey {
  return readStorage(SHELL_STORAGE_KEYS.tab) || 'chat'
}

function readLeftWidth(): number {
  const raw = readStorage(SHELL_STORAGE_KEYS.leftWidth)
  const n = raw === null ? NaN : Number(raw)
  if (!Number.isFinite(n)) return LEFT_RAIL_WIDTH.default
  return Math.min(LEFT_RAIL_WIDTH.max, Math.max(LEFT_RAIL_WIDTH.min, Math.round(n)))
}

const activePanel = ref<PanelKey>(readPanel())
const activeTab = ref<ResultTabKey>(readTab())
const collapsed = ref(readCollapsed())
const leftWidth = ref(readLeftWidth())
const drawerOpen = ref(false)
const settingsOpen = ref(false)
const isNarrow = useNarrowScreen()

// FD.4 对接：对话视图里的内嵌结果卡经此把中栏切到对应结果标签（activeTab 是唯一事实源）。
provideResultTabNav((key) => {
  activeTab.value = key
})

watch(activePanel, (v) => writeStorage(SHELL_STORAGE_KEYS.panel, v))
watch(activeTab, (v) => writeStorage(SHELL_STORAGE_KEYS.tab, v))
watch(leftWidth, (v) => writeStorage(SHELL_STORAGE_KEYS.leftWidth, String(v)))

function toggleCollapsed(): void {
  collapsed.value = !collapsed.value
  writeStorage(SHELL_STORAGE_KEYS.collapsed, collapsed.value ? '1' : '0')
}

function onOpenSettings(): void {
  settingsOpen.value = true
}

// 启动恢复：载入对话列表 → 回到上次的当前对话；并校验中栏当前标签是否仍存在，
// 不存在（例如所属对话已变）则回落「对话」。
onMounted(async () => {
  await conversations.load()
  conversations.restoreCurrent()
})

watch(
  () => conversations.currentId,
  () => {
    const keys = ['chat', ...artifacts.openIds(conversations.currentId)]
    if (!keys.includes(activeTab.value)) activeTab.value = 'chat'
  },
)
</script>

<template>
  <div class="app-shell">
    <TopBar @open-settings="onOpenSettings" />

    <div class="app-shell__body">
      <LeftRail
        v-model:active="activePanel"
        v-model:drawer-open="drawerOpen"
        v-model:width="leftWidth"
        :collapsed="collapsed"
        :narrow="isNarrow"
        @toggle="toggleCollapsed"
      >
        <component :is="PANEL_VIEWS[activePanel]" />
      </LeftRail>

      <CenterTabs v-model:active="activeTab" class="app-shell__center" />
    </div>

    <SettingsModal v-model:show="settingsOpen" />
  </div>
</template>

<style scoped>
.app-shell {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--n-body-color, #f5f7fb);
}
.app-shell__body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
}
.app-shell__center {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
</style>
