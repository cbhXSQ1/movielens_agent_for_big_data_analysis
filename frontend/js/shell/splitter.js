/* 竖向分隔条。spec §5.4：常态有 3 个 grip 短杠做视觉引导，悬停/拖动变色，
   键盘可达（←/→ 16px、Shift 48px、Home/End、Enter 或双击复位）。 */
export const MIN_W = 280;
export const MAX_W = 720;
export const DEFAULT_W = 380;

export function clampWidth(px, min = MIN_W, max = MAX_W) {
  return Math.max(min, Math.min(max, Math.round(px)));
}

export function nextWidth(current, key, shift, min = MIN_W, max = MAX_W) {
  const step = shift ? 48 : 16;
  switch (key) {
    case 'ArrowRight': return clampWidth(current + step, min, max);
    case 'ArrowLeft':  return clampWidth(current - step, min, max);
    case 'Home':       return min;
    case 'End':        return max;
    case 'Enter': case ' ': return DEFAULT_W;
    default:           return current;
  }
}

export function createSplitter({ node, store, min = MIN_W, max = MAX_W }) {
  let dragging = false;

  function apply(px, { persist = false } = {}) {
    const w = clampWidth(px, min, max);
    document.documentElement.style.setProperty('--rail-w', w + 'px');
    node.setAttribute('aria-valuenow', String(w));
    node.dataset.active = String(w !== DEFAULT_W);
    store.set(s => ({ ui: { ...s.ui, railWidth: w } }));
    if (persist) { try { localStorage.setItem('mlgov.railWidth', String(w)); } catch { /* 忽略隐私模式 */ } }
    return w;
  }

  function usable() { return window.innerWidth > 1180; }

  function onPointerDown(e) {
    if (!usable()) return;
    dragging = true;
    node.setPointerCapture(e.pointerId);
    document.body.classList.add('is-resizing');
  }
  function onPointerMove(e) {
    if (!dragging) return;
    apply(store.get().ui.railWidth + e.movementX);
  }
  function onPointerUp(e) {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('is-resizing');
    try { node.releasePointerCapture(e.pointerId); } catch { /* 已释放 */ }
    apply(store.get().ui.railWidth, { persist: true });
  }

  return {
    start(initial) {
      apply(initial == null ? DEFAULT_W : initial);
      node.addEventListener('pointerdown', onPointerDown);
      node.addEventListener('pointermove', onPointerMove);
      node.addEventListener('pointerup', onPointerUp);
      node.addEventListener('dblclick', () => apply(DEFAULT_W, { persist: true }));
      node.addEventListener('keydown', e => {
        if (!usable()) return;
        const w = nextWidth(store.get().ui.railWidth, e.key, e.shiftKey, min, max);
        if (w !== store.get().ui.railWidth) {
          e.preventDefault();
          apply(w, { persist: true });
        }
      });
    },
  };
}
