/* 视图注册契约：加一个视图 = 这个文件 + css/views/<id>.css + main.js 里 VIEWS 加一项。 */
import { el } from '../core/dom.js';

export default {
  id: 'cleaning',
  title: '清洗',
  order: 30,
  css: 'css/views/cleaning.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2 4h12M2 8h8M2 12h5" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    root.appendChild(el('p', 'view__placeholder', '清洗'));
  },

  update(state) {
    this.root.dataset.hasResult = String(!!state.result);
  },
};
