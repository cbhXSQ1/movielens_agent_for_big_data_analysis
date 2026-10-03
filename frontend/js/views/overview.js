/* 视图注册契约：加一个视图 = 这个文件 + css/views/<id>.css + main.js 里 VIEWS 加一项。 */
import { el } from '../core/dom.js';

export default {
  id: 'overview',
  title: '总览',
  order: 10,
  css: 'css/views/overview.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="2" y="2" width="5" height="12" fill="none" stroke="currentColor" stroke-width="1.7"/><rect x="9" y="2" width="5" height="7" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    root.appendChild(el('p', 'view__placeholder', '总览'));
  },

  update(state) {
    this.root.dataset.hasResult = String(!!state.result);
  },
};
