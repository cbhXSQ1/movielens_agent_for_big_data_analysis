/* 视图注册契约：加一个视图 = 这个文件 + css/views/<id>.css + main.js 里 VIEWS 加一项。 */
import { el } from '../core/dom.js';

export default {
  id: 'evidence',
  title: '证据',
  order: 40,
  css: 'css/views/evidence.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3 2h7l3 3v9H3z M10 2v3h3" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    root.appendChild(el('p', 'view__placeholder', '证据'));
  },

  update(state) {
    this.root.dataset.hasResult = String(!!state.result);
  },
};
