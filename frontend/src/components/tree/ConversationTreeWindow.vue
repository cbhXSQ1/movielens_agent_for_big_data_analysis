<script setup lang="ts">
// 阶段 F · 对话树浮窗（重做版）。
// - 一个节点 = 一个「回合」（一次提问 + 一次回复）；不再把 user/assistant 画成两个框。
// - 布局：竖着的 Git 提交图（自上而下、每条分支占一条泳道），见 treeLayout.ts。
// - 圆点：当前回合高亮；悬停/点击弹缩略显示该回合的**提问与回复**（loadNode 取内容）。
// - 圆点旁的「在此继续」= 从该回合的**末尾节点**另起一支（branchFrom(tipId)），
//   使整轮（问 + 答）留在新支里，只把该轮之后的消息当作旧支隐藏。
// - 保留：大画布 + 横/纵滑块平移（overflow:auto）、刷新、关闭（点击遮罩/按钮/Esc）。
// 组件不直接 fetch：读树/读节点/切分支全部经 useTreeStore。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NEmpty, NSpin } from 'naive-ui'
import { useConversationsStore } from '@/stores/conversations'
import { useTreeStore } from '@/stores/tree'
import type { NodeId } from '@/api/types'
import {
  ROUND_R,
  layoutTree,
  type PositionedRound,
  type TreeEdge,
} from './treeLayout'

const show = defineModel<boolean>('show', { default: false })

const conversations = useConversationsStore()
const tree = useTreeStore()

const cid = computed(() => conversations.currentId)
const layout = computed(() => layoutTree(tree.nodes))

/** 当前所在节点落在哪个回合上（用于高亮）。 */
const currentRoundId = computed<NodeId | null>(() => {
  const nodeId = tree.currentNodeId
  if (nodeId === null) return null
  const hit = layout.value.rounds.find((p) =>
    p.round.nodes.some((n) => n.node_id === nodeId),
  )
  return hit ? hit.round.id : null
})

/** 悬停优先、点击固定：缩略窗当前展示的回合。 */
const hoveredId = ref<NodeId | null>(null)
const selectedId = ref<NodeId | null>(null)
const detailId = computed(() => hoveredId.value ?? selectedId.value)
const detailRound = computed<PositionedRound | null>(() => {
  const id = detailId.value
  if (id === null) return null
  return layout.value.rounds.find((p) => p.round.id === id) ?? null
})

/** 提问：优先用已加载的 user 节点内容，未加载则先显示摘要。 */
const detailQuestion = computed(() => {
  const pos = detailRound.value
  if (!pos) return ''
  const userNode = pos.round.user
  const loaded = userNode ? tree.nodeDetails[userNode.node_id]?.content : ''
  const text = (loaded && loaded.trim()) || pos.round.question || '（无内容）'
  return text.trim()
})

/** 回复：本回合内各 assistant/task 节点的内容（未加载先回落摘要）拼接。 */
const detailReply = computed(() => {
  const pos = detailRound.value
  if (!pos) return ''
  return pos.round.nodes
    .filter((n) => n.role !== 'user')
    .map((n) => tree.nodeDetails[n.node_id]?.content || n.summary || '')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .join('\n\n')
})

const canvasStyle = computed(() => ({
  width: `${layout.value.width}px`,
  height: `${layout.value.height}px`,
}))

function roundStyle(pos: PositionedRound): Record<string, string> {
  return {
    left: `${pos.x - ROUND_R}px`,
    top: `${pos.y - ROUND_R}px`,
  }
}

const thumbStyle = computed(() => {
  const pos = detailRound.value
  if (!pos) return {}
  return {
    left: `${pos.x + ROUND_R + 12}px`,
    top: `${pos.y + ROUND_R + 8}px`,
  }
})

/** 跨泳道连线：父中点 → 子中点的竖向贝塞尔曲线。 */
function edgePath(edge: TreeEdge): string {
  const dy = (edge.y2 - edge.y1) / 2
  return `M ${edge.x1} ${edge.y1} C ${edge.x1} ${edge.y1 + dy} ${edge.x2} ${
    edge.y2 - dy
  } ${edge.x2} ${edge.y2}`
}

function loadRoundContent(pos: PositionedRound): void {
  const id = cid.value
  if (!id) return
  for (const node of pos.round.nodes) void tree.loadNode(id, node.node_id)
}

async function refresh(): Promise<void> {
  const id = cid.value
  if (!id) {
    tree.reset()
    return
  }
  await tree.load(id)
}

function onHover(pos: PositionedRound): void {
  hoveredId.value = pos.round.id
  loadRoundContent(pos)
}

function onLeave(): void {
  hoveredId.value = null
}

function onSelect(pos: PositionedRound): void {
  selectedId.value = pos.round.id
  loadRoundContent(pos)
}

async function onSwitch(pos: PositionedRound): Promise<void> {
  const id = cid.value
  if (!id) return
  const fromNodeId = pos.round.tipId ?? pos.round.userId ?? pos.round.id
  await tree.branchFrom(id, fromNodeId)
}

function close(): void {
  show.value = false
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') close()
}

watch(
  show,
  (open) => {
    if (open) {
      void refresh()
      window.addEventListener('keydown', onKeydown)
    } else {
      window.removeEventListener('keydown', onKeydown)
    }
  },
  { immediate: true },
)

watch(cid, () => {
  if (show.value) void refresh()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div
    v-if="show"
    class="tree-window"
    data-testid="tree-window"
    role="dialog"
    aria-modal="true"
    aria-label="对话树"
  >
    <div class="tree-window__backdrop" data-testid="tree-backdrop" @click="close" />

    <section class="tree-window__panel">
      <header class="tree-window__bar">
        <span class="tree-window__title">对话树</span>
        <span class="tree-window__conv" data-testid="tree-conv">
          {{ cid ?? '未选择对话' }}
        </span>
        <span class="tree-window__spacer" />
        <n-button
          size="tiny"
          quaternary
          :loading="tree.loading"
          data-testid="tree-refresh"
          @click="refresh"
        >
          刷新
        </n-button>
        <n-button size="tiny" quaternary data-testid="tree-close" @click="close">
          关闭
        </n-button>
      </header>

      <div class="tree-window__body">
        <p v-if="!cid" class="tree-window__hint" data-testid="tree-no-conv">
          未选择对话
        </p>

        <n-spin v-else :show="tree.loading">
          <n-empty
            v-if="!tree.hasTree"
            description="暂无节点"
            class="tree-window__empty"
            data-testid="tree-empty"
          />

          <div
            v-else
            class="tree-canvas"
            data-testid="tree-canvas"
            :style="canvasStyle"
          >
            <svg
              class="tree-canvas__edges"
              :width="layout.width"
              :height="layout.height"
            >
              <template v-for="edge in layout.edges" :key="`${edge.from}-${edge.to}`">
                <line
                  v-if="!edge.curved"
                  class="tree-canvas__edge"
                  data-testid="tree-edge"
                  :x1="edge.x1"
                  :y1="edge.y1"
                  :x2="edge.x2"
                  :y2="edge.y2"
                />
                <path
                  v-else
                  class="tree-canvas__edge"
                  data-testid="tree-edge"
                  :d="edgePath(edge)"
                />
              </template>
            </svg>

            <div
              v-for="pos in layout.rounds"
              :key="pos.round.id"
              class="tree-round"
              :class="{
                'tree-round--current': pos.round.id === currentRoundId,
                'tree-round--selected': pos.round.id === selectedId,
              }"
              :style="roundStyle(pos)"
              :data-testid="`tree-node-${pos.round.id}`"
              @mouseenter="onHover(pos)"
              @mouseleave="onLeave"
              @click="onSelect(pos)"
            >
              <span class="tree-round__dot" aria-hidden="true" />
              <span class="tree-round__label">
                <span class="tree-round__summary">
                  {{ pos.round.question || '（无提问）' }}
                </span>
                <n-button
                  class="tree-round__switch"
                  size="tiny"
                  secondary
                  type="primary"
                  :disabled="pos.round.id === currentRoundId"
                  :data-testid="`tree-switch-${pos.round.id}`"
                  @click.stop="onSwitch(pos)"
                >
                  在此继续
                </n-button>
              </span>
            </div>

            <div
              v-if="detailRound"
              class="tree-thumb"
              data-testid="tree-thumb"
              :style="thumbStyle"
            >
              <div class="tree-thumb__role">
                回合 · #{{ detailRound.round.id }}
              </div>
              <div class="tree-thumb__block">
                <span class="tree-thumb__tag">提问</span>
                <div class="tree-thumb__text" data-testid="tree-thumb-text">
                  {{ detailQuestion }}
                </div>
              </div>
              <div class="tree-thumb__block">
                <span class="tree-thumb__tag">回复</span>
                <div class="tree-thumb__reply" data-testid="tree-thumb-reply">
                  {{ detailReply || '（无回复）' }}
                </div>
              </div>
            </div>
          </div>
        </n-spin>

        <p v-if="tree.lastError" class="tree-window__error" data-testid="tree-error">
          {{ tree.lastError }}
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.tree-window {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.tree-window__backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
}
.tree-window__panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: min(1100px, 92vw);
  height: min(80vh, 760px);
  min-width: 420px;
  min-height: 320px;
  overflow: hidden;
  border-radius: 12px;
  background: var(--n-color, #ffffff);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.28);
  resize: both;
}
.tree-window__bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--n-border-color, #efeff5);
}
.tree-window__title {
  color: var(--n-text-color, #333);
  font-size: var(--fs-lg);
  font-weight: 600;
}
.tree-window__conv {
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-md);
}
.tree-window__spacer {
  flex: 1 1 auto;
}
.tree-window__body {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  /* 横/纵滑块平移：图又长又宽时出现滚动条拖动平移 */
  overflow: auto;
}
.tree-window__hint,
.tree-window__error {
  margin: 0;
  padding: 12px;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-md);
}
.tree-window__error {
  color: var(--n-error-color, #d03050);
}
.tree-window__empty {
  padding: 48px 0;
}
.tree-canvas {
  position: relative;
  min-width: 100%;
  min-height: 100%;
}
.tree-canvas__edges {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
}
.tree-canvas__edge {
  fill: none;
  stroke: var(--n-border-color, #cfd3dc);
  stroke-width: 1.5;
}
.tree-round {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 32px;
  cursor: pointer;
}
.tree-round__dot {
  box-sizing: border-box;
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border: 2px solid var(--n-border-color, #cfd3dc);
  border-radius: 50%;
  background: var(--n-color, #ffffff);
}
.tree-round:hover .tree-round__dot {
  border-color: var(--n-primary-color, #2f6fed);
}
.tree-round--selected .tree-round__dot {
  box-shadow: 0 0 0 3px var(--n-primary-color-opacity, rgba(47, 111, 237, 0.35));
}
.tree-round--current .tree-round__dot {
  border-color: var(--n-primary-color, #2f6fed);
  background: var(--n-primary-color, #2f6fed);
  box-shadow: 0 0 0 3px var(--n-primary-color-opacity, rgba(47, 111, 237, 0.35));
}
.tree-round__label {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 168px;
}
.tree-round__summary {
  overflow: hidden;
  flex: 0 1 auto;
  color: var(--n-text-color, #333);
  font-size: var(--fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-round__switch {
  flex: 0 0 auto;
  opacity: 0;
  transition: opacity 0.15s;
}
.tree-round:hover .tree-round__switch,
.tree-round--selected .tree-round__switch,
.tree-round--current .tree-round__switch {
  opacity: 1;
}
.tree-thumb {
  position: absolute;
  z-index: 5;
  width: 280px;
  max-height: 260px;
  overflow: hidden;
  padding: 8px 10px;
  border: 1px solid var(--n-border-color, #d9dde3);
  border-radius: 8px;
  background: var(--n-color, #ffffff);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.18);
  pointer-events: none;
}
.tree-thumb__role {
  margin-bottom: 6px;
  color: var(--n-text-color-3, #999);
  font-size: var(--fs-sm);
}
.tree-thumb__block + .tree-thumb__block {
  margin-top: 8px;
}
.tree-thumb__tag {
  display: inline-block;
  margin-bottom: 2px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--n-primary-color-opacity, rgba(47, 111, 237, 0.12));
  color: var(--n-primary-color, #2f6fed);
  font-size: var(--fs-xs);
}
.tree-thumb__text,
.tree-thumb__reply {
  color: var(--n-text-color, #333);
  font-size: var(--fs-md);
  line-height: 1.5;
  word-break: break-word;
  white-space: pre-wrap;
}
</style>
