<script setup lang="ts">
// 新前端 · 中栏标签 + 内容容器。
// 固定 7 标签：对话 / 五维评分 / 评估报告.md / metrics.json / 方案依据 / 迭代二模型 / 知识图谱。
// 标签可关闭（除「对话」）；关闭只关视图，不删产物；全关回「对话」。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 .tabs / .content / .view
import { computed, watch, type Component } from 'vue'
import { FIXED_TABS, type ResultTabKey } from '@/components/shellConfig'
import { useCenterTabsStore } from '@/stores/centerTabs'
import { resourceViewFor } from '@/components/centerTabViews'
import ChatView from '@/views/tabs/ChatView.vue'
import ScoreView from '@/views/tabs/ScoreView.vue'
import ReportView from '@/views/tabs/ReportView.vue'
import MetricsView from '@/views/tabs/MetricsView.vue'
import BasisView from '@/views/tabs/BasisView.vue'
import MlView from '@/views/tabs/MlView.vue'
import KgView from '@/views/tabs/KgView.vue'

const active = defineModel<ResultTabKey>('active', { default: 'chat' })

const props = defineProps<{
  visibleTabs: ResultTabKey[]
}>()

const emit = defineEmits<{
  (e: 'close', key: ResultTabKey): void
}>()

const VIEW_COMPONENTS: Record<ResultTabKey, Component> = {
  chat: ChatView,
  score: ScoreView,
  report: ReportView,
  metrics: MetricsView,
  basis: BasisView,
  ml: MlView,
  kg: KgView,
}

const centerTabs = useCenterTabsStore()

const fixedTabs = computed(() =>
  FIXED_TABS.filter((t) => props.visibleTabs.includes(t.key)),
)

/** 标签栏 = 固定结果标签 + 资源标签（模型 / 方案 / 文件）。 */
const tabs = computed(() => [
  ...fixedTabs.value,
  ...centerTabs.list().map((t) => ({ key: t.key, label: t.label, closable: true })),
])

const activeResourceTab = computed(
  () => centerTabs.list().find((t) => t.key === active.value) ?? null,
)

const activeView = computed(() => {
  const resource = activeResourceTab.value
  if (resource) return resourceViewFor(resource.kind)
  return VIEW_COMPONENTS[active.value] ?? ChatView
})

/** 资源标签渲染组件的 props（固定标签无 props）。 */
const activeViewProps = computed(() =>
  activeResourceTab.value ? { ...activeResourceTab.value.payload } : {},
)

function selectTab(key: ResultTabKey): void {
  active.value = key
}

function onClose(key: ResultTabKey): void {
  const isResource = centerTabs.list().some((t) => t.key === key)
  if (isResource) {
    centerTabs.close(key)
    if (active.value === key) active.value = 'chat'
    return
  }
  emit('close', key)
}

// 新打开的资源标签自动切到前台：点「查看 / 预览」后立刻可见。
watch(
  () => centerTabs.list().map((t) => t.key).join('|'),
  (_now, before) => {
    const prev = new Set(String(before ?? '').split('|').filter(Boolean))
    const added = centerTabs.list().find((t) => !prev.has(t.key))
    if (added) active.value = added.key
  },
)
</script>

<template>
  <section class="center-tabs" data-testid="center-tabs">
    <nav class="tabs" role="tablist" aria-label="结果标签">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ active: active === tab.key }"
        role="tab"
        :aria-selected="active === tab.key"
        :data-testid="`tab-${tab.key}`"
        @click="selectTab(tab.key)"
      >
        {{ tab.label }}
        <span
          v-if="tab.closable"
          class="close"
          role="button"
          :aria-label="`关闭 ${tab.label}`"
          :data-testid="`tab-close-${tab.key}`"
          @click.stop="onClose(tab.key)"
        >×</span>
      </button>
    </nav>

    <div class="content">
      <div class="view active" :data-view="active">
        <component :is="activeView" v-bind="activeViewProps" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.center-tabs {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}
.content {
  flex: 1;
  min-height: 0;
  position: relative;
}
</style>
