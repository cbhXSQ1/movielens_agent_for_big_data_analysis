<script setup lang="ts">
// 新前端 · 中栏标签 + 内容容器。
// 固定 7 标签：对话 / 五维评分 / 评估报告.md / metrics.json / 方案依据 / 迭代二模型 / 知识图谱。
// 标签可关闭（除「对话」）；关闭只关视图，不删产物；全关回「对话」。
// 视觉基线：docs/frontend/new-frontend-demo.html 的 .tabs / .content / .view
import { computed, type Component } from 'vue'
import { FIXED_TABS, type ResultTabKey } from '@/components/shellConfig'
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

const tabs = computed(() =>
  FIXED_TABS.filter((t) => props.visibleTabs.includes(t.key)),
)

const activeView = computed(() => VIEW_COMPONENTS[active.value])

function selectTab(key: ResultTabKey): void {
  active.value = key
}

function onClose(key: ResultTabKey): void {
  emit('close', key)
}
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
        <component :is="activeView" />
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
