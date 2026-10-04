/* 对话时间线。Agent 气泡上标"谁处理的"（规则解析 / 大模型编排）——
   这是最诚实的做法，也是被追问时一眼能答的证据（spec §7.1）。

   B6（plan §2.2）：左栏原先只有两种气泡，任务那条线一个字都没有 —— 用户看不出"我发的这句话"
   与任务条上那个任务是同一件事。现在每一轮任务在时间线里都有系统行：已提交 / 开始跑 /
   进入某阶段 / 已完成 / 失败。判据全在 `taskline.js`（纯函数），这里只负责摆放与滚动。

   两个决定：
   - **行不写进 `store.timeline`**：那份数组的形状是 `{role,text}` 的消息，任务条的 `render`
     会读它；往里塞第三种形状的东西是把风险摊给别的模块。这里自己留一份（`sysLines` / `sysMark`），
     每次重绘按锚点插进去 —— 判据与呈现都关在左栏内部。
   - **锚点 = 生成那一刻的消息条数**，所以"已提交"永远紧跟发起它的那句用户消息，阶段推进跟在
     Agent 那条回复后面；后面又来了新消息时历史行也不会被挤到队尾。 */
import { el, clear } from '../core/dom.js';
import { renderSteps } from '../ui/steps.js';
import { taskLines, lineAttrs } from './taskline.js';

const ENGINE_ZH = { rules: '规则解析', llm: '大模型编排', chat_llm: '大模型编排' };

export function createTimeline({ host, store }) {
  let sysLines = [];      // [{ anchor, ...line, rendered }]，anchor = 该行前面应有的消息条数
  let sysMark = { id: null, status: null, stage: null, seeded: false };   // 上一拍的任务状态（去重靠它）
  let sysId = null;       // 这份系统行属于哪个任务号：换号即整份清掉重来

  /* 一次 store 变更 → 该多出哪几行。任务状态每 3 秒刷新一次，状态与阶段都没变时这里是空数组。 */
  function pull(state) {
    const task = state.task || {};
    if (task.id !== sysId) { sysLines = []; sysId = task.id || null; }
    const { lines, next } = taskLines(sysMark, task, sysMark.seeded);
    sysMark = next;
    for (const l of lines) sysLines.push({ ...l, anchor: (state.timeline || []).length, rendered: false });
  }

  function render(state) {
    pull(state);
    const list = state.timeline || [];
    clear(host);
    for (const l of sysLines) l.rendered = false;
    if (list.length === 0 && sysLines.length === 0) {
      host.appendChild(el('p', 'timeline__intro', '说一句话就能发起清洗与评估。'));
      return;
    }
    /* `at` = 已经画完的消息条数。锚点等于它的行排在**下一条消息之前**；
       锚点没被任何消息夹住的（比如任务在最后一条消息之后才结束）由末次 `flush` 补上。 */
    let at = 0;
    const flush = () => {
      for (const l of sysLines) {
        if (!l.rendered && l.anchor <= at) { host.appendChild(sysRow(l)); l.rendered = true; }
      }
    };
    for (const item of list) {
      flush();
      host.appendChild(item.role === 'user' ? userBubble(item) : agentBubble(item));
      at++;
    }
    flush();
    host.scrollTop = host.scrollHeight;     // 左栏是可滚容器：新行自己滚到底（既有行为，沿用）
  }

  return { render };
}

/* 一条系统行。细、浅、左侧 2px 竖线 —— 一眼看得出"这不是你说的话，也不是 Agent 说的话"。
   墨色系（`--fg-3`），**不用**橙色：橙色实心块全页只有发送键与任务条进度两处（G7）。
   `mark` 是状态标记（`aria-hidden`，读屏时不念符号），文字才是要念的内容。 */
function sysRow(l) {
  const { cls, mark, text, live } = lineAttrs(l);
  const row = el('div', cls);
  row.setAttribute('aria-live', live);      // 阶段推进显式 off：照旧可读，但不打扰
  const sym = el('span', 'timeline__sys-mark', mark);
  sym.setAttribute('aria-hidden', 'true');
  row.appendChild(sym);
  row.appendChild(el('span', 'timeline__sys-text', text));
  return row;
}

function userBubble(item) {
  const box = el('div', 'msg msg--user');
  box.appendChild(el('p', 'msg__text', item.text));
  return box;
}

function agentBubble(item) {
  const box = el('div', `msg msg--agent${item.error ? ' msg--error' : ''}`);
  const head = el('div', 'msg__head');
  if (item.engine) {
    head.appendChild(el('span', 'badge', ENGINE_ZH[item.engine] || item.engine));
  }
  if (item.intentCn) head.appendChild(el('span', 'msg__intent', item.intentCn));
  if (head.childNodes.length) box.appendChild(head);

  box.appendChild(el('p', 'msg__text', item.text || ''));

  const details = el('div', 'msg__steps');
  if (renderSteps(details, item.steps)) box.appendChild(details);
  return box;
}
