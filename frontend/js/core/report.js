/* 报告渲染。只支持报告实际用到的子集，未知语法降级为 <pre> 原文，不猜（spec §11-R2）。
   顺序很重要：先全量转义，再做替换。 */
export function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

export function renderReport(md) {
  const lines = String(md || '').split(/\r?\n/);
  const out = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[-\s|:]+\|\s*$/.test(lines[i + 1] || '')) {
      const head = cells(line);
      out.push('<table><thead><tr>' +
        head.map(c => `<th scope="col">${inline(c)}</th>`).join('') + '</tr></thead><tbody>');
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        out.push('<tr>' + cells(lines[i]).map(c => `<td>${inline(c)}</td>`).join('') + '</tr>');
        i++;
      }
      out.push('</tbody></table>');
      continue;
    }

    if (/^#{1,6}\s+/.test(line)) {
      out.push(`<h3>${inline(line.replace(/^#{1,6}\s+/, ''))}</h3>`);
      i++; continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`);
        i++;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }

    if (line.trim() === '') { i++; continue; }

    if (/^\s{4,}\S/.test(line) || /^```/.test(line)) {
      const buf = [];
      while (i < lines.length && lines[i].trim() !== '' && !/^#{1,6}\s/.test(lines[i])) { buf.push(lines[i]); i++; }
      out.push(`<pre>${esc(buf.join('\n'))}</pre>`);
      continue;
    }

    /* 裁定 R16：整行加粗（报告 §5 的 4 个小标题就是这个形状）按 brief 的段落判定会被
       首字符 `*` 挡在 <p> 之外，落进 <pre> 里带着字面量 `**` 一起显示 —— 而 `**…**`
       正是本模块 inline() 支持的、brief 测试里断言过的语法，不属于「未知语法」。
       最小修正：整行加粗先收成段落，其余不变（`- ` / `* ` 仍由上面的列表分支接走）。 */
    if (/^\*\*[^*]+\*\*\s*$/.test(line)) {
      out.push(`<p>${inline(line)}</p>`);
      i++; continue;
    }

    /* 裁定 R16：brief 的测试要求 `::: 奇怪语法 :::` 落到 <pre>（「未知语法降级为 pre，不猜」），
       但同一份实现的段落判定把冒号开头的行收成 <p>，7 个测试里必挂 1 个。
       取舍以测试为准（Step 7 写的是「7 个测试全过」），最小修正：冒号也不算段落起始。 */
    if (/^\S/.test(line) && !/^[|#\-*`:]/.test(line)) {
      out.push(`<p>${inline(line)}</p>`);
      i++; continue;
    }

    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== '') { buf.push(lines[i]); i++; }
    out.push(`<pre>${esc(buf.join('\n'))}</pre>`);
  }
  return out.join('\n');
}

function cells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(s => s.trim());
}

/* D3：把报告里的关键数字包成可点链接，跳到对应视图。
   裁定 R16（实测修正）：brief 给的三条正则对**真实报告**命中 0 个链接 ——
   报告里没有「综合」二字（综合分那一行写的是 `| composite | 95.07 | …`），
   而「隔离总数：**100830**」渲染成 HTML 后关键词与数字之间隔了 11 个字符（`总数：<strong>`），
   超过 `{0,10}` 的上限。spec §4.4 的 D3 要求这些数字**真的可点**，
   所以最小修正：补 `composite` 别名、窗口放宽到 16。 */
const GOTO = [
  [/(?:综合(?:质量分|分)?|composite)[^0-9]{0,16}(\d+\.\d+)/g, 'overview'],
  [/隔离[^0-9]{0,16}([\d,]{3,})/g, 'cleaning'],
  [/输入[^0-9]{0,16}([\d,]{3,})/g, 'cleaning'],
];

export function linkifyNumbers(html) {
  let out = html;
  for (const [re, target] of GOTO) {
    out = out.replace(re, m => m.replace(/([\d][\d,\.]*)/, n => `<a href="#/${target}" data-goto="${target}">${n}</a>`));
  }
  return out;
}
