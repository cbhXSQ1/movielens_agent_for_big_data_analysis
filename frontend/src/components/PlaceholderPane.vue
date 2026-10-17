<script setup lang="ts">
// 阶段 C · 占位面板/视图的通用外壳。
// 后续阶段用具体实现替换对应 view 组件即可；也可通过默认插槽就地注入内容。
import { NEmpty } from 'naive-ui'

defineProps<{
  testid: string
  title: string
  stage: string
  hint?: string
}>()
</script>

<template>
  <div class="placeholder-pane" :data-testid="testid">
    <n-empty :description="`${title} · 由 ${stage} 实现`">
      <template #extra>
        <p v-if="hint" class="placeholder-pane__hint">{{ hint }}</p>
        <slot />
      </template>
    </n-empty>
  </div>
</template>

<style scoped>
.placeholder-pane {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 32px 16px;
}
.placeholder-pane__hint {
  max-width: 360px;
  margin: 8px 0 0;
  color: var(--n-text-color-3, #999);
  font-size: 13px;
  line-height: 1.7;
  text-align: center;
}
</style>
