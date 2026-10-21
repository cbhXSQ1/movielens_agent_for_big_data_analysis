<script setup lang="ts">
// 左栏②「对话树」缩略预览：把「回合 + 泳道」的**竖向 Git 图**用 SVG viewBox
// 自动缩到小框里，呈现迷你地图。只作预览、不逐节点交互；点击整块由父组件开浮窗。
import { computed } from 'vue'
import type { NodeId, TreeNode } from '@/api/types'
import { ROUND_R, layoutTree, type TreeEdge } from './treeLayout'

const props = defineProps<{
  nodes: TreeNode[]
  currentNodeId?: NodeId | null
}>()

defineEmits<{ (e: 'open'): void }>()

const layout = computed(() => layoutTree(props.nodes))
const viewBox = computed(() => `0 0 ${layout.value.width} ${layout.value.height}`)

const currentRoundId = computed<NodeId | null>(() => {
  const id = props.currentNodeId
  if (id === null || id === undefined) return null
  const hit = layout.value.rounds.find((p) =>
    p.round.nodes.some((n) => n.node_id === id),
  )
  return hit ? hit.round.id : null
})

function edgePath(edge: TreeEdge): string {
  const dy = (edge.y2 - edge.y1) / 2
  return `M ${edge.x1} ${edge.y1} C ${edge.x1} ${edge.y1 + dy} ${edge.x2} ${
    edge.y2 - dy
  } ${edge.x2} ${edge.y2}`
}
</script>

<template>
  <button
    type="button"
    class="tree-thumbnail"
    data-testid="tree-thumbnail"
    title="对话树"
    @click="$emit('open')"
  >
    <svg
      v-if="layout.rounds.length"
      class="tree-thumbnail__svg"
      :viewBox="viewBox"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      <template v-for="edge in layout.edges" :key="`${edge.from}-${edge.to}`">
        <line
          v-if="!edge.curved"
          class="tree-thumbnail__edge"
          :x1="edge.x1"
          :y1="edge.y1"
          :x2="edge.x2"
          :y2="edge.y2"
        />
        <path v-else class="tree-thumbnail__edge" :d="edgePath(edge)" />
      </template>
      <circle
        v-for="pos in layout.rounds"
        :key="pos.round.id"
        class="tree-thumbnail__dot"
        :class="{
          'tree-thumbnail__dot--current': pos.round.id === currentRoundId,
        }"
        :cx="pos.x"
        :cy="pos.y"
        :r="ROUND_R"
        data-testid="tree-thumbnail-dot"
      />
    </svg>
    <span v-else class="tree-thumbnail__empty">暂无节点</span>
  </button>
</template>

<style scoped>
.tree-thumbnail {
  display: block;
  box-sizing: border-box;
  width: 100%;
  height: 200px;
  padding: 8px;
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: var(--ml-radius-lg, 12px);
  background: var(--ml-surface-soft, #f7f8fa);
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.tree-thumbnail:hover {
  border-color: var(--n-primary-color, #2f6fed);
}
.tree-thumbnail__svg {
  display: block;
  width: 100%;
  height: 100%;
}
.tree-thumbnail__edge {
  fill: none;
  stroke: #cfd3dc;
  stroke-width: 2;
}
.tree-thumbnail__dot {
  fill: #ffffff;
  stroke: #d9dde3;
  stroke-width: 2;
}
.tree-thumbnail__dot--current {
  fill: rgba(47, 111, 237, 0.2);
  stroke: var(--n-primary-color, #2f6fed);
  stroke-width: 4;
}
.tree-thumbnail__empty {
  display: flex;
  height: 100%;
  align-items: center;
  justify-content: center;
  color: var(--n-text-color-3, #6b7280);
  font-size: var(--ml-text-sm, 15px);
}
</style>
