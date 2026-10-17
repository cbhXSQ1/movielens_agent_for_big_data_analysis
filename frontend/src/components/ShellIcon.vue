<script setup lang="ts">
// 轻量内联图标（无第三方图标依赖）。以 currentColor 描边，随文本色/主题变化。
import { computed } from 'vue'

const props = defineProps<{
  name: string
  size?: number
}>()

const ICONS: Record<string, string> = {
  list:
    '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/>' +
    '<path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
  tree:
    '<rect x="9" y="3" width="6" height="5" rx="1"/>' +
    '<rect x="3" y="16" width="6" height="5" rx="1"/>' +
    '<rect x="15" y="16" width="6" height="5" rx="1"/>' +
    '<path d="M12 8v4"/><path d="M6 16v-2h12v2"/>',
  resources:
    '<ellipse cx="12" cy="6" rx="8" ry="3"/>' +
    '<path d="M4 6v12c0 1.66 3.58 3 8 3s8-1.34 8-3V6"/>' +
    '<path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>',
  progress:
    '<path d="M3 3v18h18"/>' +
    '<rect x="7" y="12" width="3" height="6"/>' +
    '<rect x="12" y="8" width="3" height="10"/>' +
    '<rect x="17" y="5" width="3" height="13"/>',
  settings:
    '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>' +
    '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>' +
    '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>' +
    '<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/>' +
    '<line x1="17" y1="16" x2="23" y2="16"/>',
  status: '<circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/>',
  refresh:
    '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 4v5h-5"/>',
  'chevron-left': '<path d="M15 18l-6-6 6-6"/>',
  'chevron-right': '<path d="M9 18l6-6-6-6"/>',
}

const markup = computed(() => ICONS[props.name] ?? '')
const px = computed(() => props.size ?? 20)
</script>

<template>
  <svg
    :width="px"
    :height="px"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    v-html="markup"
  />
</template>
