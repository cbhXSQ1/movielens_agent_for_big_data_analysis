/* D3 的落点（spec §4.4：点报告里的数字 → 跳转**并高亮**对应卡片）。
   跳转由 router 的 viewParams 带过来（`#/scores?hl=Accurate`），高亮按 key 找目标节点：
   目标用 `data-hl` 声明，所以这里不需要知道任何视图的 DOM 结构（G14）。
   key 为空就什么都不做；上一个高亮会被摘掉，1600ms 后自己消失。
   `prefers-reduced-motion` 不用在这里判断：CSS 只给类加轮廓、没有动画。 */

const HL_MS = 1600;

export function highlightKey(host, key) {
  if (!host) return null;
  for (const old of host.querySelectorAll('.is-hl')) old.classList.remove('is-hl');
  if (!key) return null;
  let target = null;
  for (const node of host.querySelectorAll('[data-hl]')) {
    if (node.dataset.hl === key) { target = node; break; }
  }
  if (!target) return null;
  target.classList.add('is-hl');
  setTimeout(() => target.classList.remove('is-hl'), HL_MS);
  return target;
}
