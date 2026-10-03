/* 唯一状态源。变更一律走 set()，视图只读 get()。 */
function shallowEqual(a, b) {
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every(k => Object.is(a[k], b[k]));
}

export function createStore(initial) {
  let state = Object.freeze({ ...initial });
  const listeners = new Set();

  return {
    get() { return state; },

    set(patch) {
      const next = typeof patch === 'function' ? patch(state) : patch;
      const merged = Object.freeze({ ...state, ...next });
      if (shallowEqual(state, merged)) return;
      state = merged;
      for (const fn of [...listeners]) fn(state);
    },

    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
