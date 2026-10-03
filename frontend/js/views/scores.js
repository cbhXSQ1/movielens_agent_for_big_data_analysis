/* 视图注册契约：加一个视图 = 这个文件 + css/views/<id>.css + main.js 里 VIEWS 加一项。 */
import { el } from '../core/dom.js';

export default {
  id: 'scores',
  title: '五维',
  order: 20,
  css: 'css/views/scores.css',
  icon: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 2 14 6.5v6L8 14 2 12.5v-6z" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',

  mount(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    root.appendChild(el('p', 'view__placeholder', '五维'));
  },

  update(state) {
    this.root.dataset.hasResult = String(!!state.result);
  },
};
