/* hash 路由 + 视图注册表。导航栏与路由都从 VIEWS 生成。 */

export function parseRoute(hash) {
  const m = /^#\/([a-z0-9-]*)(?:\?(.*))?$/.exec(hash || '');
  const id = (m && m[1]) || 'overview';
  const params = {};
  if (m && m[2]) {
    for (const part of m[2].split('&')) {
      if (!part) continue;
      const i = part.indexOf('=');
      const k = decodeURIComponent(i < 0 ? part : part.slice(0, i));
      const v = i < 0 ? '' : decodeURIComponent(part.slice(i + 1));
      if (k) params[k] = v;
    }
  }
  return { id: id || 'overview', params };
}

export function resolveRoute(route, views) {
  return views.find(v => v.id === route.id) || views.find(v => v.id === 'overview');
}

export function routeHref(id, params) {
  const q = params && Object.keys(params).length
    ? '?' + Object.keys(params).map(k => `${encodeURIComponent(k)}=${encodeURIComponent(params[k])}`).join('&')
    : '';
  return `#/${id}${q}`;
}

/* ---- DOM 部分：装配导航与视图挂载 ---- */
export function createRouter({ views, navHost, viewHost, store, ctx }) {
  const mounted = new Map();
  const loadedCss = new Set();

  function ensureCss(href) {
    if (!href || loadedCss.has(href)) return;
    loadedCss.add(href);
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    document.head.appendChild(link);
  }

  function renderNav(current) {
    navHost.textContent = '';
    for (const v of views) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'nav__item';
      btn.dataset.view = v.id;
      btn.setAttribute('aria-current', v.id === current ? 'page' : 'false');
      btn.innerHTML = v.icon;
      const label = document.createElement('span');
      label.textContent = v.title;
      btn.appendChild(label);
      btn.addEventListener('click', () => { location.hash = routeHref(v.id); });
      navHost.appendChild(btn);
    }
  }

  function show(route) {
    const view = resolveRoute(route, views);
    ensureCss(view.css);
    mounted.get(viewHost.dataset.current)?.destroy?.();
    viewHost.textContent = '';
    viewHost.dataset.current = view.id;
    const el = document.createElement('section');
    el.className = 'view';
    el.id = `view-${view.id}`;
    viewHost.appendChild(el);
    view.mount(el, ctx);
    mounted.set(view.id, view);
    renderNav(view.id);
    store.set({ view: view.id, viewParams: route.params });
  }

  return {
    start() {
      const apply = () => show(parseRoute(location.hash));
      window.addEventListener('hashchange', apply);
      apply();
      store.subscribe(state => {
        const cur = state.view;
        if (viewHost.dataset.current === cur) {
          const view = views.find(v => v.id === cur);
          if (view && !state.suspendViewUpdate) view.update(state);
        }
      });
    },
    go(id, params) { location.hash = routeHref(id, params); },
  };
}
