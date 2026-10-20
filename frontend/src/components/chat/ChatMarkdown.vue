<script setup lang="ts">
// 阶段 D+ · 聊天消息的 Markdown 渲染：助手回答按 Markdown 排版显示
// （标题 / 列表 / 代码块 / 表格 / 行内样式），不再把 `**`、`#` 等原文符号
// 直接怼给用户。复用 results/markdown.ts 的同一份解析（无第三方库、
// 不用 v-html，文本由 Vue 自动转义）。
//
// 数字护栏标红（guard spans）在这里保留：护栏给的是**原文区间**，先按区间
// 取「针段」把行内文本切开、标红，再对每段做行内 Markdown 解析 ——
// 于是「99.95」这类未溯源数字在渲染后的回答里依然带波浪线提示。
import { computed } from 'vue'
import {
  parseInline,
  parseMarkdown,
  type InlineSegment,
} from '@/components/results/markdown'
import type { GuardSpan } from '@/api/types'

const props = defineProps<{
  text: string
  /** 数字护栏区间（原文偏移）；渲染后按片段匹配标红。 */
  warningSpans?: GuardSpan[]
}>()

/** 行内渲染单元：行内 Markdown 段 + 是否护栏标红。 */
interface InlineToken {
  type: InlineSegment['type']
  text: string
  href?: string
  warn: boolean
}

type BlockVM =
  | { kind: 'heading'; level: number; tokens: InlineToken[] }
  | { kind: 'paragraph'; tokens: InlineToken[] }
  | { kind: 'quote'; tokens: InlineToken[] }
  | { kind: 'list'; ordered: boolean; items: InlineToken[][] }
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'hr' }
  | { kind: 'table'; header: InlineToken[][]; rows: InlineToken[][][] }

/** 护栏针段：span 覆盖的原文片段（过滤越界/空段）。 */
const needles = computed<string[]>(() => {
  const text = props.text ?? ''
  return (props.warningSpans ?? [])
    .filter(
      (span) =>
        Number.isInteger(span.start) &&
        Number.isInteger(span.end) &&
        span.start >= 0 &&
        span.end > span.start &&
        span.end <= text.length,
    )
    .map((span) => text.slice(span.start, span.end))
    .filter((piece) => piece.trim().length > 0)
})

/** 把一段行内文本按护栏针段切开（合并重叠区间）。 */
function splitByNeedles(text: string): Array<{ text: string; warn: boolean }> {
  if (!needles.value.length) return [{ text, warn: false }]
  const hits: Array<[number, number]> = []
  for (const needle of needles.value) {
    let from = 0
    for (;;) {
      const at = text.indexOf(needle, from)
      if (at < 0) break
      hits.push([at, at + needle.length])
      from = at + needle.length
    }
  }
  if (!hits.length) return [{ text, warn: false }]
  hits.sort((a, b) => a[0] - b[0])
  const merged: Array<[number, number]> = []
  for (const hit of hits) {
    const last = merged[merged.length - 1]
    if (last && hit[0] <= last[1]) last[1] = Math.max(last[1], hit[1])
    else merged.push([...hit])
  }
  const out: Array<{ text: string; warn: boolean }> = []
  let cursor = 0
  for (const [start, end] of merged) {
    if (start > cursor) out.push({ text: text.slice(cursor, start), warn: false })
    out.push({ text: text.slice(start, end), warn: true })
    cursor = end
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), warn: false })
  return out
}

/** 行内文本 → 渲染单元（先切护栏，再做行内 Markdown 解析）。 */
function inlineTokens(text: string): InlineToken[] {
  const tokens: InlineToken[] = []
  for (const piece of splitByNeedles(text)) {
    for (const segment of parseInline(piece.text)) {
      tokens.push({ ...segment, warn: piece.warn })
    }
  }
  return tokens
}

const blocks = computed<BlockVM[]>(() => {
  const out: BlockVM[] = []
  for (const block of parseMarkdown(props.text ?? '')) {
    switch (block.type) {
      case 'heading':
        out.push({ kind: 'heading', level: block.level, tokens: inlineTokens(block.text) })
        break
      case 'paragraph':
        out.push({ kind: 'paragraph', tokens: inlineTokens(block.text) })
        break
      case 'quote':
        out.push({ kind: 'quote', tokens: inlineTokens(block.text) })
        break
      case 'list':
        out.push({
          kind: 'list',
          ordered: block.ordered,
          items: block.items.map((item) => inlineTokens(item)),
        })
        break
      case 'code':
        out.push({ kind: 'code', lang: block.lang, text: block.text })
        break
      case 'table':
        out.push({
          kind: 'table',
          header: block.header.map((cell) => inlineTokens(cell)),
          rows: block.rows.map((row) => row.map((cell) => inlineTokens(cell))),
        })
        break
      case 'hr':
        out.push({ kind: 'hr' })
        break
    }
  }
  return out
})
</script>

<template>
  <div class="chat-markdown" data-testid="chat-markdown">
    <template v-for="(block, index) in blocks" :key="index">
      <component
        :is="`h${Math.min(block.kind === 'heading' ? block.level : 3, 6)}`"
        v-if="block.kind === 'heading'"
        class="chat-markdown__heading"
        :data-level="block.level"
      >
        <template v-for="(token, i) in block.tokens" :key="i">
          <code v-if="token.type === 'code'" class="chat-markdown__code">{{ token.text }}</code>
          <strong v-else-if="token.type === 'bold'">{{ token.text }}</strong>
          <a
            v-else-if="token.type === 'link'"
            :href="token.href"
            target="_blank"
            rel="noopener noreferrer"
            class="chat-markdown__link"
            >{{ token.text }}</a
          >
          <span v-else-if="token.warn" class="chat-markdown__num-warn" :title="'未能在工具结果中找到：' + token.text">{{
            token.text
          }}</span>
          <template v-else>{{ token.text }}</template>
        </template>
      </component>

      <pre v-else-if="block.kind === 'code'" class="chat-markdown__code-block"><code>{{ block.text }}</code></pre>

      <blockquote v-else-if="block.kind === 'quote'" class="chat-markdown__quote">
        <template v-for="(token, i) in block.tokens" :key="i">
          <code v-if="token.type === 'code'" class="chat-markdown__code">{{ token.text }}</code>
          <strong v-else-if="token.type === 'bold'">{{ token.text }}</strong>
          <span v-else-if="token.warn" class="chat-markdown__num-warn">{{ token.text }}</span>
          <template v-else>{{ token.text }}</template>
        </template>
      </blockquote>

      <hr v-else-if="block.kind === 'hr'" class="chat-markdown__hr" />

      <ul v-else-if="block.kind === 'list' && !block.ordered" class="chat-markdown__list">
        <li v-for="(item, i) in block.items" :key="i">
          <template v-for="(token, j) in item" :key="j">
            <code v-if="token.type === 'code'" class="chat-markdown__code">{{ token.text }}</code>
            <strong v-else-if="token.type === 'bold'">{{ token.text }}</strong>
            <span v-else-if="token.warn" class="chat-markdown__num-warn">{{ token.text }}</span>
            <template v-else>{{ token.text }}</template>
          </template>
        </li>
      </ul>

      <ol v-else-if="block.kind === 'list' && block.ordered" class="chat-markdown__list">
        <li v-for="(item, i) in block.items" :key="i">
          <template v-for="(token, j) in item" :key="j">
            <code v-if="token.type === 'code'" class="chat-markdown__code">{{ token.text }}</code>
            <strong v-else-if="token.type === 'bold'">{{ token.text }}</strong>
            <span v-else-if="token.warn" class="chat-markdown__num-warn">{{ token.text }}</span>
            <template v-else>{{ token.text }}</template>
          </template>
        </li>
      </ol>

      <table v-else-if="block.kind === 'table'" class="chat-markdown__table">
        <thead>
          <tr>
            <th v-for="(cell, i) in block.header" :key="i">
              <template v-for="(token, j) in cell" :key="j">
                <strong v-if="token.type === 'bold'">{{ token.text }}</strong>
                <span v-else-if="token.warn" class="chat-markdown__num-warn">{{ token.text }}</span>
                <template v-else>{{ token.text }}</template>
              </template>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, r) in block.rows" :key="r">
            <td v-for="(cell, c) in row" :key="c">
              <template v-for="(token, j) in cell" :key="j">
                <code v-if="token.type === 'code'" class="chat-markdown__code">{{ token.text }}</code>
                <strong v-else-if="token.type === 'bold'">{{ token.text }}</strong>
                <span v-else-if="token.warn" class="chat-markdown__num-warn">{{ token.text }}</span>
                <template v-else>{{ token.text }}</template>
              </template>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-else-if="block.kind === 'paragraph'" class="chat-markdown__paragraph">
        <template v-for="(token, i) in block.tokens" :key="i">
          <code v-if="token.type === 'code'" class="chat-markdown__code">{{ token.text }}</code>
          <strong v-else-if="token.type === 'bold'">{{ token.text }}</strong>
          <a
            v-else-if="token.type === 'link'"
            :href="token.href"
            target="_blank"
            rel="noopener noreferrer"
            class="chat-markdown__link"
            >{{ token.text }}</a
          >
          <span v-else-if="token.warn" class="chat-markdown__num-warn" :title="'未能在工具结果中找到：' + token.text">{{
            token.text
          }}</span>
          <template v-else>{{ token.text }}</template>
        </template>
      </p>
    </template>
  </div>
</template>

<style scoped>
.chat-markdown {
  font-size: var(--ml-text-md, 15px);
  line-height: 1.75;
  word-break: break-word;
}
.chat-markdown__heading {
  margin: 14px 0 6px;
  font-weight: 600;
  line-height: 1.4;
}
.chat-markdown__heading[data-level='1'] { font-size: 1.15em; }
.chat-markdown__heading[data-level='2'] { font-size: 1.1em; }
.chat-markdown__heading[data-level='3'] { font-size: 1.02em; }
.chat-markdown__paragraph {
  margin: 0 0 8px;
}
.chat-markdown__paragraph:last-child { margin-bottom: 0; }
.chat-markdown__list {
  margin: 4px 0 8px;
  padding-left: 20px;
}
.chat-markdown__list li { margin: 2px 0; }
.chat-markdown__quote {
  margin: 4px 0 8px;
  padding: 2px 12px;
  border-left: 3px solid var(--n-border-color, #d9dde3);
  color: var(--n-text-color-2, #555);
}
.chat-markdown__code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--n-code-color, #f3f4f6);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.92em;
}
.chat-markdown__code-block {
  margin: 6px 0 10px;
  padding: 10px 12px;
  overflow-x: auto;
  border-radius: 8px;
  background: var(--n-code-color, #f3f4f6);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.86em;
  line-height: 1.6;
}
.chat-markdown__table {
  width: 100%;
  margin: 6px 0 10px;
  border-collapse: collapse;
  font-size: 0.92em;
}
.chat-markdown__table th,
.chat-markdown__table td {
  padding: 5px 8px;
  border: 1px solid var(--n-border-color, #e5e7eb);
  text-align: left;
}
.chat-markdown__table th {
  background: var(--n-action-color, rgba(0, 0, 0, 0.03));
  font-weight: 600;
}
.chat-markdown__hr {
  margin: 10px 0;
  border: none;
  border-top: 1px solid var(--n-border-color, #e5e7eb);
}
.chat-markdown__link {
  color: var(--n-primary-color, #2f6fed);
  text-underline-offset: 2px;
}
.chat-markdown__num-warn {
  color: var(--n-error-color, #d03050);
  text-decoration: underline wavy var(--n-error-color, #d03050);
  text-underline-offset: 3px;
  cursor: help;
}
</style>
