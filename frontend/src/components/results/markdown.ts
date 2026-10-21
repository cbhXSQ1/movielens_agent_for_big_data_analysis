// 阶段 E · FE.4：轻量 Markdown 解析（不引入任何 Markdown 库）。
// 只解析报告常用子集：标题 / 段落 / 无序·有序列表 / 代码块 / 引用 / 分割线 / 表格，
// 以及行内的 `code`、**粗体**、[链接](url)。渲染由 MarkdownView.vue 用 Vue 模板完成，
// 全程不使用 v-html，文本由 Vue 自动转义，避免注入。

export interface InlineSegment {
  type: 'text' | 'code' | 'bold' | 'link'
  text: string
  href?: string
}

export type MarkdownBlock =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'code'; lang: string; text: string }
  | { type: 'quote'; text: string }
  | { type: 'hr' }
  | { type: 'table'; header: string[]; rows: string[][] }

const INLINE_PATTERN = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)]+\))/g

/** 解析行内标记为若干段：纯文本 / 行内代码 / 粗体 / 链接。 */
export function parseInline(text: string): InlineSegment[] {
  const segments: InlineSegment[] = []
  let lastIndex = 0
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const index = match.index ?? 0
    if (index > lastIndex) {
      segments.push({ type: 'text', text: text.slice(lastIndex, index) })
    }
    const token = match[0]
    if (token.startsWith('`')) {
      segments.push({ type: 'code', text: token.slice(1, -1) })
    } else if (token.startsWith('**')) {
      segments.push({ type: 'bold', text: token.slice(2, -2) })
    } else {
      const label = token.slice(1, token.indexOf(']'))
      const href = token.slice(token.indexOf('](') + 2, -1)
      segments.push({ type: 'link', text: label, href })
    }
    lastIndex = index + token.length
  }
  if (lastIndex < text.length) {
    segments.push({ type: 'text', text: text.slice(lastIndex) })
  }
  return segments
}

function splitRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  return trimmed.split('|').map((cell) => cell.trim())
}

const TABLE_SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/
const LIST_UNORDERED = /^\s*[-*+]\s+(.*)$/
const LIST_ORDERED = /^\s*\d+[.)]\s+(.*)$/
const HEADING = /^(#{1,6})\s+(.*)$/

/** 把 Markdown 全文解析为块序列。 */
export function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  const blocks: MarkdownBlock[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    if (line.trim() === '') {
      i += 1
      continue
    }

    if (line.trimStart().startsWith('```')) {
      const lang = line.trim().slice(3).trim()
      const code: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
        code.push(lines[i])
        i += 1
      }
      i += 1
      blocks.push({ type: 'code', lang, text: code.join('\n') })
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      blocks.push({
        type: 'heading',
        level: heading[1].length,
        text: heading[2].trim(),
      })
      i += 1
      continue
    }

    if (/^(---|\*\*\*|___)\s*$/.test(line.trim())) {
      blocks.push({ type: 'hr' })
      i += 1
      continue
    }

    if (
      line.includes('|') &&
      i + 1 < lines.length &&
      TABLE_SEPARATOR.test(lines[i + 1])
    ) {
      const header = splitRow(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
        rows.push(splitRow(lines[i]))
        i += 1
      }
      blocks.push({ type: 'table', header, rows })
      continue
    }

    if (LIST_UNORDERED.test(line)) {
      const items: string[] = []
      while (i < lines.length) {
        const m = LIST_UNORDERED.exec(lines[i])
        if (!m) break
        items.push(m[1].trim())
        i += 1
      }
      blocks.push({ type: 'list', ordered: false, items })
      continue
    }

    if (LIST_ORDERED.test(line)) {
      const items: string[] = []
      while (i < lines.length) {
        const m = LIST_ORDERED.exec(lines[i])
        if (!m) break
        items.push(m[1].trim())
        i += 1
      }
      blocks.push({ type: 'list', ordered: true, items })
      continue
    }

    if (line.trimStart().startsWith('>')) {
      const quote: string[] = []
      while (i < lines.length && lines[i].trimStart().startsWith('>')) {
        quote.push(lines[i].trimStart().replace(/^>\s?/, ''))
        i += 1
      }
      blocks.push({ type: 'quote', text: quote.join(' ') })
      continue
    }

    const paragraph: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !HEADING.test(lines[i]) &&
      !lines[i].trimStart().startsWith('```') &&
      !lines[i].trimStart().startsWith('>') &&
      !LIST_UNORDERED.test(lines[i]) &&
      !LIST_ORDERED.test(lines[i]) &&
      !/^(---|\*\*\*|___)\s*$/.test(lines[i].trim())
    ) {
      paragraph.push(lines[i].trim())
      i += 1
    }
    blocks.push({ type: 'paragraph', text: paragraph.join(' ') })
  }

  return blocks
}
