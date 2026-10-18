<script setup lang="ts">
// 阶段 C · FC.2 / FC.3 / FC.4：左栏。
// - 图标条 ×4（列表 / 树 / 资源 / 进度），点图标切换面板内容。
// - 宽屏：面板内联，展开推挤中栏；收起只留图标条（collapsed）。
//   面板右缘有**可拖拽分隔条**（宽屏、展开态）：拖动改宽、双击复位、方向键微调。
// - 窄屏（≤1100px）：图标条常驻，面板收入左侧抽屉，点图标滑出覆盖（drawerOpen）。
import { computed, onBeforeUnmount } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { LEFT_PANELS, LEFT_RAIL_WIDTH, type PanelKey } from './shellConfig'
import ShellIcon from './ShellIcon.vue'

const active = defineModel<PanelKey>('active', { required: true })
const drawerOpen = defineModel<boolean>('drawerOpen', { default: false })
/** 内联面板宽度（px）：由壳层持有并持久化，分隔条拖拽时通过它回写。 */
const width = defineModel<number>('width', { default: LEFT_RAIL_WIDTH.default })

const props = defineProps<{
  collapsed: boolean
  narrow: boolean
}>()

const emit = defineEmits<{ (e: 'toggle'): void }>()

const activeLabel = computed(
  () => LEFT_PANELS.find((p) => p.key === active.value)?.label ?? '',
)

/** 分隔条是否可用：仅宽屏且面板展开时有意义。 */
const resizable = computed(() => !props.narrow && !props.collapsed)

function clampWidth(value: number): number {
  return Math.min(
    LEFT_RAIL_WIDTH.max,
    Math.max(LEFT_RAIL_WIDTH.min, Math.round(value)),
  )
}

// 拖拽：以指针横坐标为基准，按位移调整宽度（不依赖面板实测位置）。
let dragging = false
let startX = 0
let startWidth = 0

function onSplitterPointerMove(event: PointerEvent): void {
  if (!dragging) return
  width.value = clampWidth(startWidth + (event.clientX - startX))
}

function onSplitterPointerUp(): void {
  dragging = false
  window.removeEventListener('pointermove', onSplitterPointerMove)
  window.removeEventListener('pointerup', onSplitterPointerUp)
}

function onSplitterPointerDown(event: PointerEvent): void {
  if (!resizable.value) return
  dragging = true
  startX = event.clientX
  startWidth = width.value
  window.addEventListener('pointermove', onSplitterPointerMove)
  window.addEventListener('pointerup', onSplitterPointerUp)
}

function onSplitterDoubleClick(): void {
  width.value = LEFT_RAIL_WIDTH.default
}

function onSplitterKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    width.value = clampWidth(width.value - LEFT_RAIL_WIDTH.step)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    width.value = clampWidth(width.value + LEFT_RAIL_WIDTH.step)
  }
}

onBeforeUnmount(onSplitterPointerUp)

function selectPanel(key: PanelKey): void {
  active.value = key
  if (props.narrow) {
    // 窄屏：点图标从左侧滑出抽屉覆盖中栏。
    drawerOpen.value = true
  } else if (props.collapsed) {
    // 宽屏收起态下点图标：顺手展开，让用户看到内容。
    emit('toggle')
  }
}
</script>

<template>
  <aside
    class="left-rail"
    :class="{
      'left-rail--collapsed': collapsed && !narrow,
      'left-rail--narrow': narrow,
    }"
    data-testid="left-rail"
  >
    <nav class="left-rail__icons" aria-label="左栏面板">
      <button
        v-for="panel in LEFT_PANELS"
        :key="panel.key"
        type="button"
        class="rail-icon"
        :class="{ 'rail-icon--active': active === panel.key }"
        :title="panel.label"
        :aria-label="panel.label"
        :aria-pressed="active === panel.key"
        :data-testid="`panel-icon-${panel.key}`"
        @click="selectPanel(panel.key)"
      >
        <ShellIcon :name="panel.key" :size="20" />
      </button>

      <span class="left-rail__spacer" />

      <button
        v-if="!narrow"
        type="button"
        class="rail-icon rail-icon--toggle"
        :title="collapsed ? '展开面板' : '收起面板'"
        :aria-label="collapsed ? '展开面板' : '收起面板'"
        data-testid="rail-toggle"
        @click="emit('toggle')"
      >
        <ShellIcon :name="collapsed ? 'chevron-right' : 'chevron-left'" :size="18" />
      </button>
    </nav>

    <div
      v-if="!narrow && !collapsed"
      class="left-rail__panel"
      data-testid="left-panel"
      data-top-inset="true"
      :style="{ width: `${width}px`, flexBasis: `${width}px` }"
    >
      <slot />
    </div>

    <div
      v-if="resizable"
      class="left-rail__splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整左栏宽度"
      :aria-valuenow="width"
      :aria-valuemin="LEFT_RAIL_WIDTH.min"
      :aria-valuemax="LEFT_RAIL_WIDTH.max"
      data-testid="left-rail-splitter"
      tabindex="0"
      @pointerdown="onSplitterPointerDown"
      @dblclick="onSplitterDoubleClick"
      @keydown="onSplitterKeydown"
    />

    <n-drawer
      v-if="narrow"
      v-model:show="drawerOpen"
      :width="300"
      placement="left"
    >
      <n-drawer-content :title="activeLabel" closable>
        <slot />
      </n-drawer-content>
    </n-drawer>
  </aside>
</template>

<style scoped>
.left-rail {
  display: flex;
  height: 100%;
  min-height: 0;
  background: var(--n-color, #ffffff);
}
.left-rail__icons {
  display: flex;
  flex: 0 0 64px;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: 64px;
  padding: 14px 0;
  border-right: 1px solid var(--n-border-color, #e5e7eb);
}
.left-rail--narrow .left-rail__icons {
  flex-basis: 56px;
  width: 56px;
  padding: 10px 0;
}
.left-rail__spacer {
  flex: 1 1 auto;
}
.rail-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: none;
  border-radius: 12px;
  background: transparent;
  color: var(--n-text-color-2, #4b5563);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.rail-icon:hover {
  background: rgba(17, 24, 39, 0.05);
  color: var(--n-text-color-1, #111827);
}
.rail-icon:focus-visible {
  outline: 2px solid var(--n-primary-color, #2f6fed);
  outline-offset: 2px;
}
.rail-icon--active {
  background: rgba(47, 111, 237, 0.12);
  color: var(--n-primary-color, #2f6fed);
}
.rail-icon--active:hover {
  background: rgba(47, 111, 237, 0.16);
  color: var(--n-primary-color, #2f6fed);
}
.left-rail__panel {
  flex: 0 0 auto;
  min-height: 0;
  /* 顶部留白：首行不再贴着顶栏分隔线，避免观感上被压住/贴边。 */
  padding-top: var(--ml-space-2, 8px);
  overflow: auto;
  scrollbar-gutter: stable;
  border-right: 1px solid var(--n-border-color, #e5e7eb);
}
/* 右缘分隔条：仅宽屏展开态出现，拖动改宽（双击复位、方向键微调）。 */
.left-rail__splitter {
  position: relative;
  flex: 0 0 6px;
  align-self: stretch;
  cursor: col-resize;
  touch-action: none;
  background: transparent;
}
.left-rail__splitter::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 2px;
  transform: translateX(-50%);
  background: transparent;
  transition: background-color 0.15s ease;
}
.left-rail__splitter:hover::after,
.left-rail__splitter:focus-visible::after {
  background: var(--n-primary-color, #2f6fed);
}
.left-rail__splitter:focus-visible {
  outline: none;
}
</style>
