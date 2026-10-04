/* 输入框上方的建议提问弹层（T4 / plan §1.3）。
   **本文件里的字符串是界面文案，不是领域数据。** G14 管的是清洗规则 / 阈值 / 权重 /
   指标名（那些只许来自 config/*.json）；这里只是"用户可以这么问"的示例句，
   改文案不需要动任何逻辑，也不代表任何默认取值。
   两套建议分开存放，切换判据只有一个：左栏有没有用户气泡
   （`store.timeline` 里有没有 `role: 'user'`，那正是时间线画气泡的依据）。 */
import { el, clear } from '../core/dom.js';

export const SUGGESTIONS = {
  /* 首次进入（左栏还没有任何用户消息）：起手式 —— 怎么开始、先看什么。 */
  first: [
    '用默认规则清洗并评估五维质量',
    '先看看这批数据长什么样，别急着改',
    '只跑评分表清洗，跳过用户表和电影表',
  ],
  /* 追问（已经有用户消息）：深挖式 —— 往已有的结果里再问一层。 */
  follow: [
    '为什么隔离了这么多条',
    '唯一性是怎么算的',
    '把报告给我',
  ],
};

/* 弹层里始终有这一行（plan §1.3）：建议只是建议，用户随时可以自己写。 */
export const SUGGESTION_NOTE = '也可以直接自己写 —— 这只是建议';

/* `isBusy` 由 composer 给（就是发送键的灰态）：任务在跑时弹层不弹 —— 弹了也发不出去，
   只会挡住输入框上方。`onPick` 只**填入**输入框，不发送。 */
export function createSuggestions({ form, input, store, isBusy, onPick }) {
  const pop = el('div', 'sug');
  pop.hidden = true;
  const list = el('div', 'sug__list');       // listbox 只包选项；提示行是它的兄弟（ARIA 结构）
  list.id = 'sug-list';
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', '建议提问');
  /* 提示行是**常驻节点**：listbox 里只许放 option，所以它是 list 的兄弟，不参与重绘。 */
  const note = el('p', 'sug__note', SUGGESTION_NOTE);
  pop.appendChild(list);
  pop.appendChild(note);
  /* 绝对定位的基准是 `.composer`（components.css 里那段 R65 注释）：弹层**向上**开，
     所以它必须长在 form 里，而不是挂到 body 上再回头算坐标。 */
  form.appendChild(pop);
  input.setAttribute('aria-controls', 'sug-list');
  input.setAttribute('aria-expanded', 'false');

  const items = () => [...list.querySelectorAll('.sug__item')];

  function render() {
    clear(list);
    const hasUser = (store.get().timeline || []).some(e => e.role === 'user');
    for (const text of (hasUser ? SUGGESTIONS.follow : SUGGESTIONS.first)) {
      const b = el('button', 'sug__item', text);
      b.type = 'button';   // 在 <form> 里：不写 type 就是 submit，点一条会把表单发出去
      b.setAttribute('role', 'option');
      b.addEventListener('click', () => { onPick(text); close(); });
      list.appendChild(b);
    }
  }

  function open() {
    if (!pop.hidden) return;               // 已经开着：不重绘、不打断用户已经移进去的焦点
    /* 运行中（发送键 disabled）不弹；输入框里已经有字时也不弹 —— 那是在改自己写的那句。
       聚焦/点开即弹、开始输入就收起：不是每打一个字都弹（那样键盘用户会被弹层一直挡着）。 */
    if (isBusy() || input.value.trim()) return;
    render();
    pop.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    fit();
    document.addEventListener('click', onDocClick);
    document.addEventListener('keydown', onDocKey);
  }

  /* 只靠 `max-height` 挡不住上边界：窄屏（≤1180px）是纵向堆叠，输入框并不在视口底部，
     内容一多弹层就伸到视口外面（实测 1000x520 的窄屏 + 加长到 17 条建议：top = −49.75）。
     所以显示之后量一次：把上限压到"输入框上方还能放多少"，宁可自己滚，
     也不让任何一条建议跑到屏幕外。桌面上这个 clamp 从不生效（输入框在视口底部）。 */
  function fit() {
    pop.style.maxHeight = '';                                  // 先还原成 CSS 的 min(40vh, 240px)
    const room = input.getBoundingClientRect().top - 28;       // 减掉 20px 间距与 8px 离上边界
    if (pop.getBoundingClientRect().height > room) pop.style.maxHeight = Math.max(72, Math.round(room)) + 'px';
  }

  function close() {
    if (pop.hidden) return;
    pop.hidden = true;
    pop.style.maxHeight = '';              // 别把这一轮的临时上限留给下一次
    input.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', onDocClick);
    document.removeEventListener('keydown', onDocKey);
  }

  /* 点弹层外面关掉：做法与 T1 的 `.taskpick` 完全一致（document 上的 click + contains()，
     只在打开期间挂载、关闭时移除）。**禁用 `blur` 方案** —— 点建议项的第一下会先把焦点
     移走，用 blur 会点不中（plan §5）。 */
  function onDocClick(e) {
    if (pop.contains(e.target) || e.target === input) return;
    /* 例外：「新任务」这类按钮会在自己的 click 处理里把焦点送回输入框，而同一次点击又会
       冒泡到这里。此时输入框是空的、且仍持有焦点 —— 说明这一下的目的地就是输入框，
       别把刚弹出来的建议立刻关掉。焦点已经离开输入框（点到别处）时照常关。 */
    if (document.activeElement === input && !input.value.trim()) return;
    close();
  }
  function onDocKey(e) {
    if (e.key !== 'Escape') return;   // 不 preventDefault：Esc 还归别的组件（如设置弹窗）
    /* 顺序要紧：先把焦点还给输入框 —— 这一步会触发 `focus` → `open()`，
       紧接着的 `close()` 才是最终状态。反过来写（先关后聚焦）会当场又被弹开。 */
    input.focus();
    close();
  }

  /* ↑/↓：焦点在输入框时先弹出来再进列表；已经在选项上时在列表内循环。
     Enter 选中由原生 button 提供（不需要自己实现）。 */
  function move(e, fromInput) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    if (fromInput && pop.hidden) open();
    const all = items();
    const i = fromInput ? -1 : all.indexOf(document.activeElement);
    if (!all.length || (!fromInput && i < 0)) return;
    e.preventDefault();
    all[fromInput ? (e.key === 'ArrowDown' ? 0 : all.length - 1)
      : (i + (e.key === 'ArrowDown' ? 1 : all.length - 1)) % all.length].focus();
  }

  return {
    start() {
      input.addEventListener('focus', open);
      /* 「点开输入框弹建议」是这条需求的原始说法（迭代一反馈 3）：Esc 关掉之后输入框
         仍然拿着焦点，再点它不会有 focus 事件 —— 所以 click 也要挂上，否则"再点一次
         看不到了"。open() 自己幂等，点第二下不会有副作用。 */
      input.addEventListener('click', open);
      input.addEventListener('input', close);        // 开始输入就收起
      input.addEventListener('keydown', e => move(e, true));
      list.addEventListener('keydown', e => move(e, false));
      /* 任务开跑（发送键变灰）时把已经展开的弹层收掉：它这时既发不出去又挡着输入框。
         本订阅必须排在 composer 的 syncDisabled 之后 —— composer.start() 里是这么排的。 */
      store.subscribe(() => { if (isBusy()) close(); });
    },
    close,
    get isOpen() { return !pop.hidden; },
  };
}
