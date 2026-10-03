/* 视图注册契约：加一个视图 = 这个文件 + css/views/<id>.css + main.js 里 VIEWS 加一项。 */
import { el } from '../core/dom.js';

export default {
  id: 'basis',
  title: '依据',
  order: 50,
  css: 'css/views/basis.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 2h8v12H4z M6 5h4 M6 8h4 M6 11h2" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    root.appendChild(el('p', 'view__placeholder', '依据'));
  },

  update(state) {
    this.root.dataset.hasResult = String(!!state.result);
  },
};
