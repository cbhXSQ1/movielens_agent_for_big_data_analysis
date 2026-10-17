<script setup lang="ts">
// 阶段 E · FE.4：Markdown 渲染（无第三方库，不使用 v-html）。
// 报告块（report）通常是一份 Markdown 全文，这里按块渲染为语义化元素。
import { computed } from 'vue'
import MdInline from './MdInline.vue'
import { parseMarkdown } from './markdown'

const props = defineProps<{ content: string }>()

const blocks = computed(() => parseMarkdown(props.content))
</script>

<template>
  <div class="markdown-view" data-testid="markdown-view">
    <template v-for="(block, index) in blocks" :key="index">
      <component
        :is="`h${block.level}`"
        v-if="block.type === 'heading'"
        class="md-heading"
        :data-level="block.level"
      >
        <MdInline :text="block.text" />
      </component>

      <pre v-else-if="block.type === 'code'" class="md-code-block"><code>{{ block.text }}</code></pre>

      <blockquote v-else-if="block.type === 'quote'" class="md-quote">
        <MdInline :text="block.text" />
      </blockquote>

      <hr v-else-if="block.type === 'hr'" class="md-hr" />

      <ul v-else-if="block.type === 'list' && !block.ordered" class="md-list">
        <li v-for="(item, i) in block.items" :key="i"><MdInline :text="item" /></li>
      </ul>

      <ol v-else-if="block.type === 'list' && block.ordered" class="md-list">
        <li v-for="(item, i) in block.items" :key="i"><MdInline :text="item" /></li>
      </ol>

      <table v-else-if="block.type === 'table'" class="md-table">
        <thead>
          <tr>
            <th v-for="(cell, i) in block.header" :key="i"><MdInline :text="cell" /></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, r) in block.rows" :key="r">
            <td v-for="(cell, c) in row" :key="c"><MdInline :text="cell" /></td>
          </tr>
        </tbody>
      </table>

      <p v-else-if="block.type === 'paragraph'" class="md-paragraph">
        <MdInline :text="block.text" />
      </p>
    </template>
  </div>
</template>

<style scoped>
.markdown-view {
  line-height: 1.75;
  color: var(--n-text-color, #333);
  word-break: break-word;
}
.md-heading {
  margin: 18px 0 10px;
  font-weight: 600;
}
h1.md-heading {
  font-size: 20px;
}
h2.md-heading {
  font-size: 17px;
}
h3.md-heading {
  font-size: 15px;
}
.md-paragraph {
  margin: 8px 0;
}
.md-list {
  margin: 8px 0;
  padding-left: 22px;
}
.md-code-block {
  margin: 10px 0;
  padding: 10px 12px;
  overflow: auto;
  border-radius: 6px;
  background: var(--n-code-color, #f6f7f9);
  font-size: 13px;
}
.md-quote {
  margin: 10px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--n-border-color, #e0e0e6);
  color: var(--n-text-color-2, #666);
}
.md-hr {
  margin: 14px 0;
  border: none;
  border-top: 1px solid var(--n-border-color, #efeff5);
}
.md-table {
  width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  font-size: 14px;
}
.md-table th,
.md-table td {
  padding: 6px 10px;
  border: 1px solid var(--n-border-color, #efeff5);
  text-align: left;
}
.md-table th {
  background: var(--n-action-color, #f6f7f9);
}
</style>
