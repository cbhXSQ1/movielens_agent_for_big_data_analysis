<script setup lang="ts">
// 阶段 E · FE.4：行内 Markdown 渲染（`code` / **粗体** / [链接](url)）。
// 不使用 v-html，文本经 Vue 转义。
import { computed } from 'vue'
import { parseInline } from './markdown'

const props = defineProps<{ text: string }>()

const segments = computed(() => parseInline(props.text))
</script>

<template>
  <template v-for="(seg, index) in segments" :key="index">
    <code v-if="seg.type === 'code'" class="md-inline-code">{{ seg.text }}</code>
    <strong v-else-if="seg.type === 'bold'">{{ seg.text }}</strong>
    <a
      v-else-if="seg.type === 'link'"
      :href="seg.href"
      target="_blank"
      rel="noreferrer noopener"
    >{{ seg.text }}</a>
    <template v-else>{{ seg.text }}</template>
  </template>
</template>

<style scoped>
.md-inline-code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--n-action-color, #f2f3f5);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.9em;
}
</style>
