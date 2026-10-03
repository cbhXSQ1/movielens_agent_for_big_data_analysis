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

    /* 裁定 R48：上一轮为了保住旧的 `::: 奇怪语法 :::` 用例，在这里给段落判定加了 `:` 例外 ——
       方向反了（测试给代码提要求，不是代码讨好测试）。已按 R5 把用例输入换成
       `| 这不是表格\n| 第二行`：两行都**不以 `|` 结尾**，表格分支不收；首字符 `|` 也进不了
       <p>，所以整段照旧落到 <pre>，冒号例外删除、判定回到 brief 原文。 */
    if (/^\S/.test(line) && !/^[|#\-*`]/.test(line)) {
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
   裁定 R51：本模块**不含任何领域词**（G14）——目标表 `goto: [{ re, target }]` 由调用方传入，
   维度名等都由调用方从 store / 接口现算。`re` 必须带 /g；每条只包住命中片段里的第一个数字。
   标签无感知是这个函数已知的边界（renderReport 不产出带数字的标签或属性）。 */
export function linkifyNumbers(html, goto) {
  let out = html;
  for (const { re, target } of goto || []) {
    out = out.replace(re, m => m.replace(/([\d][\d,\.]*)/, n => `<a href="#/${target}" data-goto="${target}">${n}</a>`));
  }
  return out;
}
