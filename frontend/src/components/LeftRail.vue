<script setup lang="ts">
// 新前端 · 左栏：品牌头 + 图标条（4 个）+ 功能面板 + 可拖拽分隔条。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 .rail
// - 宽屏：品牌头 + 图标条 + 内联面板；点 ML Logo 或底部箭头收起/展开。
// - 窄屏（≤1100px）：只留图标条；面板从左侧抽屉滑出。
import { computed, onBeforeUnmount } from 'vue'
import { NDrawer, NDrawerContent } from 'naive-ui'
import { LEFT_PANELS, LEFT_RAIL_WIDTH, type PanelKey } from './shellConfig'

const active = defineModel<PanelKey>('active', { required: true })
const drawerOpen = defineModel<boolean>('drawerOpen', { default: false })
const width = defineModel<number>('width', { default: LEFT_RAIL_WIDTH.default })

const props = defineProps<{
  collapsed: boolean
  narrow: boolean
}>()

const emit = defineEmits<{ (e: 'toggle'): void }>()

const activeLabel = computed(
  () => LEFT_PANELS.find((p) => p.key === active.value)?.label ?? '',
)

const resizable = computed(() => !props.narrow && !props.collapsed)

function clampWidth(value: number): number {
  return Math.min(
    LEFT_RAIL_WIDTH.max,
    Math.max(LEFT_RAIL_WIDTH.min, Math.round(value)),
  )
}

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
    drawerOpen.value = true
  } else if (props.collapsed) {
    emit('toggle')
  }
}
</script>

<template>
  <div
    class="left-rail"
    :class="{ 'left-rail--narrow': narrow }"
    :style="{ '--panel-w': `${width}px` }"
    data-testid="left-rail"
  >
    <aside class="rail" :class="{ collapsed: collapsed && !narrow, resizing: dragging }">
      <div class="rail-brand">
        <button
          type="button"
          class="brand-mark brand-toggle"
          :title="collapsed ? '展开左栏' : '收起左栏'"
          :aria-label="collapsed ? '展开左栏' : '收起左栏'"
          data-testid="brand-toggle"
          @click="emit('toggle')"
        >
          ML
        </button>
        <div class="brand-text">
          <h1>MovieLens Agent</h1>
          <p>迭代二 · 数据治理与分析</p>
        </div>
      </div>

      <div class="rail-body">
        <nav class="iconbar" aria-label="左栏功能面板">
          <button
            v-for="panel in LEFT_PANELS"
            :key="panel.key"
            type="button"
            class="iconbtn"
            :class="{ active: active === panel.key }"
            :title="panel.label"
            :aria-label="panel.label"
            :aria-pressed="active === panel.key"
            :data-testid="`panel-icon-${panel.key}`"
            @click="selectPanel(panel.key)"
          >
            <!-- 对话列表 -->
            <svg v-if="panel.key === 'list'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
              <path d="M4 6h16M4 12h16M4 18h10" />
              <circle cx="2" cy="6" r="1" fill="currentColor" stroke="none" />
              <circle cx="2" cy="12" r="1" fill="currentColor" stroke="none" />
              <circle cx="2" cy="18" r="1" fill="currentColor" stroke="none" />
            </svg>
            <!-- 对话树 -->
            <svg v-else-if="panel.key === 'tree'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
              <circle cx="6" cy="5" r="2.2" />
              <circle cx="18" cy="9" r="2.2" />
              <circle cx="18" cy="19" r="2.2" />
              <path d="M6 7.2v3.3a3 3 0 0 0 3 3h3.3a3 3 0 0 1 3 3v.3" />
              <path d="M8.2 5H15a3 3 0 0 1 3 3v.8" />
            </svg>
            <!-- 资源 -->
            <svg v-else-if="panel.key === 'resources'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v8A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5v-10Z" />
            </svg>
            <!-- 任务进度 -->
            <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
              <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
            </svg>
          </button>

          <span class="spacer" />

          <button
            v-if="!narrow"
            type="button"
            class="iconbtn"
            :title="collapsed ? '展开面板' : '收起面板'"
            :aria-label="collapsed ? '展开面板' : '收起面板'"
            data-testid="rail-toggle"
            @click="emit('toggle')"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" :style="{ transform: collapsed ? 'rotate(180deg)' : 'none', transition: 'transform .18s ease' }">
              <path d="M14 6l-6 6 6 6" />
            </svg>
          </button>
        </nav>

        <div
          v-if="!narrow && !collapsed"
          class="panel"
          data-testid="left-panel"
          :style="{ width: `${width}px` }"
        >
          <slot />
        </div>
      </div>
    </aside>

    <div
      v-if="resizable"
      class="resizer"
      :class="{ active: dragging }"
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
  </div>
</template>

<style scoped>
.left-rail {
  display: flex;
  flex: 0 0 auto;
  min-height: 0;
}
</style>
