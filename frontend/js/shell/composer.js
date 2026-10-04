/* 全页唯一的输入框（spec §4.1：合并了原来的"对 Agent 说"与"追问"两个框）。
   后端本来就只有 POST /api/chat 一个入口，靠 task_id 区分发起与追问。
   裁定 R2：本模块**不需要 import dom.js** —— 全部动作都走 form.querySelector 与原生 DOM，
   早先草稿里的 `import { el } from '../core/dom.js'` 是未使用的导入。 */
import { createSuggestions } from './suggestions.js';

/* T2 / plan §1.4b（B1）：任务在 queued / running 时**禁止发送** —— 现在这会直接起一个新任务、
   覆盖掉正在轮询的那个，而旧任务在后端还在跑（数据层面的危险，不只是体验问题）。
   `liveTaskId` 仍在时也一并禁掉：点过「新任务」清空展示之后，不能留一条绕过禁令的后门。
   禁令必须看得见 —— `.btn:disabled` 与 `.composer__mode:disabled` 的灰态在 components.css。 */
const BUSY_TITLE = '任务正在跑，本版不支持取消，跑完才能发下一句';

export function createComposer({ form, store, api, onSent }) {
  const input = form.querySelector('#composer-input');
  const modeSel = form.querySelector('#exec-mode');
  const sendBtn = form.querySelector('#btn-send');
  let sending = false;          // 请求在飞：这时也必须灰着，不能被 store 的订阅改回可点

  function autoGrow() {
    input.style.height = 'auto';
    input.style.height = Math.min(240, input.scrollHeight) + 'px';
  }

  /* 光标置末尾。`setSelectionRange` 在少数输入类型上会抛，吞掉即可（与 T2 的写法一致）。 */
  function caretEnd() {
    const n = input.value.length;
    try { input.setSelectionRange(n, n); } catch { /* 忽略 */ }
  }

  /* T4（plan §1.3）：填入建议 —— **只填入，不发送**。用户可以直接接着改这一句。 */
  function fillInput(text) {
    input.value = text;
    autoGrow();
    input.focus();
    caretEnd();
  }

  /* T4（plan §1.3）：输入框上方的建议提问弹层。`isBusy` 就是发送键的灰态 ——
     任务在跑时它不弹（弹了也发不出去）。弹层只在自己打开期间占用 document 监听。 */
  const suggest = createSuggestions({
    form, input, store,
    isBusy: () => sendBtn.disabled,
    onPick: fillInput,
  });

  /* 灰态只有这一个真值来源（订阅见 start()）。`disabled` 的按钮天然不可聚焦、不触发 submit，
     读屏软件也会念出"不可用"，所以这里不需要再叠任何 aria 属性。 */
  function syncDisabled() {
    const s = store.get();
    const task = s.task || {};
    const busy = sending || !!s.liveTaskId || task.status === 'queued' || task.status === 'running';
    sendBtn.disabled = busy;
    modeSel.disabled = busy;                  // 同一时刻改不动执行方式
    sendBtn.title = busy ? BUSY_TITLE : '';
    modeSel.title = busy ? BUSY_TITLE : '';
  }

  async function submit() {
    const text = input.value.trim();
    if (!text || sendBtn.disabled) return;
    const state = store.get();
    const execMode = modeSel.value;
    const taskId = state.task && state.task.status !== 'succeeded' && state.task.status !== 'failed'
      ? state.task.id : null;

    onSent({ role: 'user', text, at: Date.now() });
    input.value = '';
    autoGrow();
    sending = true;
    syncDisabled();

    const res = await api.chat({ text, taskId, execMode, llm: state.llm });
    sending = false;
    syncDisabled();

    if (!res.ok) {
      onSent({ role: 'agent', text: `没成功：${res.error.message}`, error: true, at: Date.now() });
      return;
    }
    const d = res.data;
    onSent({
      role: 'agent',
      text: d.reply || '',
      intentCn: d.intent_cn,
      engine: d.engine,
      steps: (d.llm && d.llm.steps) || (d.data && d.data.steps) || [],
      taskId: d.task_id,
      taskStarted: !!d.task_started,
      opts: d.opts,
      at: Date.now(),
    });
  }

  return {
    start() {
      input.addEventListener('input', autoGrow);
      input.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); submit(); }
      });
      form.addEventListener('submit', e => { e.preventDefault(); submit(); });
      autoGrow();
      /* 与 shell/topbar.js 一样自订阅：main.js 已贴着 §10.13 的 250 行上限。 */
      store.subscribe(syncDisabled);
      syncDisabled();
      /* 建议弹层**必须**排在 syncDisabled 之后：它自己的订阅要读发送键的最新灰态。 */
      suggest.start();
    },
    /* T2（plan §1.4）：「新任务」清空展示后把焦点送进输入框，光标停在末尾，用户直接接着写。 */
    focusInput() {
      input.focus();
      caretEnd();
    },
    get mode() { return modeSel.value; },
  };
}
