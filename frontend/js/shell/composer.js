/* 全页唯一的输入框（spec §4.1：合并了原来的"对 Agent 说"与"追问"两个框）。
   后端本来就只有 POST /api/chat 一个入口，靠 task_id 区分发起与追问。
   裁定 R2：本模块**不需要 import dom.js** —— 全部动作都走 form.querySelector 与原生 DOM，
   早先草稿里的 `import { el } from '../core/dom.js'` 是未使用的导入。 */

export function createComposer({ form, store, api, onSent }) {
  const input = form.querySelector('#composer-input');
  const modeSel = form.querySelector('#exec-mode');
  const sendBtn = form.querySelector('#btn-send');

  function autoGrow() {
    input.style.height = 'auto';
    input.style.height = Math.min(240, input.scrollHeight) + 'px';
  }

  async function submit() {
    const text = input.value.trim();
    if (!text) return;
    const state = store.get();
    const execMode = modeSel.value;
    const taskId = state.task && state.task.status !== 'succeeded' && state.task.status !== 'failed'
      ? state.task.id : null;

    onSent({ role: 'user', text, at: Date.now() });
    input.value = '';
    autoGrow();
    sendBtn.disabled = true;

    const res = await api.chat({ text, taskId, execMode, llm: state.llm });
    sendBtn.disabled = false;

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
    },
    get mode() { return modeSel.value; },
    set disabled(v) { sendBtn.disabled = v; input.disabled = v; },
  };
}
