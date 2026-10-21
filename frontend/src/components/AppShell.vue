<script setup lang="ts">
// 新前端外壳根组件：三栏布局（左栏功能面板 / 中栏对话+结果标签 / 右栏只读上下文）。
// 视觉与交互基线：docs/frontend/new-frontend-demo.html
// - 左栏：品牌头 + 图标条（4 个）+ 功能面板，可收起。
// - 中栏：中央顶栏（标题/版本/状态标签/连接/右栏开关/设置）+ 固定 7 标签 + 内容。
// - 右栏：只读上下文摘要，可收起；窄屏变浮层。
import { computed, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import TopBar from '@/components/TopBar.vue'
import LeftRail from '@/components/LeftRail.vue'
import CenterTabs from '@/components/CenterTabs.vue'
import RightRail from '@/components/RightRail.vue'
import SettingsModal from '@/components/SettingsModal.vue'
import ConversationTreeWindow from '@/components/tree/ConversationTreeWindow.vue'
import ConversationListPanel from '@/views/panels/ConversationListPanel.vue'
import ConversationTreePanel from '@/views/panels/ConversationTreePanel.vue'
import ResourcesPanel from '@/views/panels/ResourcesPanel.vue'
import TasksPanel from '@/views/panels/TasksPanel.vue'
import { useNarrowScreen } from '@/composables/useNarrowScreen'
import { provideResultTabNav } from '@/composables/useResultTabNav'
import { provideTreeWindowOpen } from '@/composables/useTreeWindowOpen'
import { useConnectionStore } from '@/stores/connection'
import { useConversationsStore } from '@/stores/conversations'
import { useTasksStore } from '@/stores/tasks'
import {
  SHELL_STORAGE_KEYS,
  LEFT_RAIL_WIDTH,
  CLOSABLE_TAB_KEYS,
  type PanelKey,
  type ResultTabKey,
} from '@/components/shellConfig'

/** 左栏面板 key → 组件。 */
const PANEL_VIEWS: Record<PanelKey, Component> = {
  list: ConversationListPanel,
  tree: ConversationTreePanel,
  resources: ResourcesPanel,
  progress: TasksPanel,
}

const PANELS: PanelKey[] = ['list', 'tree', 'resources', 'progress']

const conversations = useConversationsStore()
const tasks = useTasksStore()
const connection = useConnectionStore()

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
  const v = readStorage(SHELL_STORAGE_KEYS.tab) as ResultTabKey | null
  return v || 'chat'
}

function readLeftWidth(): number {
  const raw = readStorage(SHELL_STORAGE_KEYS.leftWidth)
  const n = raw === null ? NaN : Number(raw)
  if (!Number.isFinite(n)) return LEFT_RAIL_WIDTH.default
  return Math.min(LEFT_RAIL_WIDTH.max, Math.max(LEFT_RAIL_WIDTH.min, Math.round(n)))
}

function readOpenTabs(): ResultTabKey[] {
  try {
    const raw = readStorage(SHELL_STORAGE_KEYS.openTabs)
    if (!raw) return []
    const arr = JSON.parse(raw) as unknown
    if (!Array.isArray(arr)) return []
    return arr.filter((x): x is ResultTabKey =>
      CLOSABLE_TAB_KEYS.includes(x as ResultTabKey),
    )
  } catch {
    return []
  }
}

function readRightCollapsed(): boolean {
  return readStorage(SHELL_STORAGE_KEYS.rightCollapsed) === '1'
}

const activePanel = ref<PanelKey>(readPanel())
const activeTab = ref<ResultTabKey>(readTab())
const collapsed = ref(readCollapsed())
const leftWidth = ref(readLeftWidth())
const drawerOpen = ref(false)
const settingsOpen = ref(false)
const treeOpen = ref(false)
/** 已打开的可关闭标签集合（控制可见性，关闭不删数据）。 */
const openTabs = ref<Set<ResultTabKey>>(new Set(readOpenTabs()))
const rightCollapsed = ref(readRightCollapsed())
const rightOpen = ref(false)
const isNarrow = useNarrowScreen()

const isNarrowRight = computed(() => isNarrow.value || window.innerWidth <= 1360)

/** 中栏标签列表：基础「对话」始终在；可关闭标签按 openTabs 决定可见。 */
const visibleTabs = computed<ResultTabKey[]>(() => {
  const list: ResultTabKey[] = ['chat']
  for (const key of CLOSABLE_TAB_KEYS) {
    if (openTabs.value.has(key)) list.push(key)
  }
  return list
})

provideResultTabNav((key) => {
  openTab(key as ResultTabKey)
  activeTab.value = key as ResultTabKey
})

provideTreeWindowOpen(() => {
  treeOpen.value = true
})

watch(activePanel, (v) => writeStorage(SHELL_STORAGE_KEYS.panel, v))
watch(activeTab, (v) => writeStorage(SHELL_STORAGE_KEYS.tab, v))
watch(leftWidth, (v) => writeStorage(SHELL_STORAGE_KEYS.leftWidth, String(v)))
watch(collapsed, (v) =>
  writeStorage(SHELL_STORAGE_KEYS.collapsed, v ? '1' : '0'),
)
watch(rightCollapsed, (v) =>
  writeStorage(SHELL_STORAGE_KEYS.rightCollapsed, v ? '1' : '0'),
)
watch(openTabs, (set) => {
  writeStorage(SHELL_STORAGE_KEYS.openTabs, JSON.stringify([...set]))
}, { deep: true })

function toggleCollapsed(): void {
  collapsed.value = !collapsed.value
}

function openTab(key: ResultTabKey): void {
  if (key !== 'chat') openTabs.value.add(key)
  activeTab.value = key
}

function closeTab(key: ResultTabKey): void {
  if (key === 'chat') return
  openTabs.value.delete(key)
  if (activeTab.value === key) activeTab.value = 'chat'
}

function openSettings(): void {
  settingsOpen.value = true
}

/** 点击右栏里的结果入口：开标签；窄屏时自动关浮层。 */
function openResultFromRight(key: ResultTabKey): void {
  openTab(key)
  if (isNarrowRight.value) rightOpen.value = false
}

function toggleRight(): void {
  if (isNarrowRight.value) {
    rightOpen.value = !rightOpen.value
  } else {
    rightCollapsed.value = !rightCollapsed.value
  }
}

function closeRight(): void {
  if (isNarrowRight.value) rightOpen.value = false
  else rightCollapsed.value = true
}

function openRight(): void {
  if (isNarrowRight.value) rightOpen.value = true
  else rightCollapsed.value = false
}

/** 状态标签点击 → 打开任务进度面板。 */
function onStatusClick(): void {
  activePanel.value = 'progress'
  if (isNarrow.value) drawerOpen.value = true
  else if (collapsed.value) collapsed.value = false
}

onMounted(async () => {
  await conversations.load()
  conversations.restoreCurrent()
  if (activeTab.value !== 'chat' && !openTabs.value.has(activeTab.value)) {
    activeTab.value = 'chat'
  }
})

watch(
  () => conversations.currentId,
  () => {
    activeTab.value = 'chat'
  },
)

// 监听窗口宽度变化：宽屏 → 窄屏时清理浮层状态。
watch(isNarrow, () => {
  rightOpen.value = false
  if (isNarrow.value) rightCollapsed.value = false
})

// 长任务自动补拉（清单①/⑤）：有活跃任务时每 5 秒补拉一次，SSE 丢事件也能自愈；
// 左栏切走「任务进度」面板时该面板会卸载，轮询放在壳层才不断。
watch(
  () => `${connection.connected}|${conversations.currentId ?? ''}`,
  (key) => {
    const [connected, cid] = key.split('|')
    if (connected === 'true' && cid) tasks.startPolling(cid)
    else tasks.stopPolling()
  },
  { immediate: true },
)

onUnmounted(() => {
  tasks.stopPolling()
})
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'right-collapsed': rightCollapsed && !isNarrowRight,
      'right-open': rightOpen,
      'drawer-open': drawerOpen,
    }"
    data-testid="app-shell"
  >
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

    <main class="main">
      <TopBar
        :task="tasks.current"
        :result="tasks.currentResult"
        @open-settings="openSettings"
        @toggle-right="toggleRight"
        @status-click="onStatusClick"
      />
      <CenterTabs
        v-model:active="activeTab"
        :visible-tabs="visibleTabs"
        @close="closeTab"
      />
    </main>

    <RightRail
      :task="tasks.current"
      :result="tasks.currentResult"
      :latest="tasks.latest"
      :collapsed="rightCollapsed && !isNarrowRight"
      @open-result="openResultFromRight"
      @collapse="closeRight"
    />

    <button
      v-if="isNarrowRight"
      class="right-edge"
      type="button"
      title="展开右栏"
      aria-label="展开右栏"
      @click="openRight"
    >
      上下文 ›
    </button>

    <div
      v-if="isNarrowRight"
      class="right-backdrop"
      @click="rightOpen = false"
    />

    <ConversationTreeWindow v-model:show="treeOpen" />
    <SettingsModal v-model:show="settingsOpen" />
  </div>
</template>

<style scoped>
.app-shell {
  position: fixed;
  inset: 0;
  display: flex;
  overflow: hidden;
  background: var(--bg);
}
.main {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
.right-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(29, 30, 34, 0.28);
  z-index: 35;
  display: none;
}
.app-shell.right-open .right-backdrop {
  display: block;
}
</style>
