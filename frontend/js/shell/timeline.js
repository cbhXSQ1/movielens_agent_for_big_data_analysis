/* 对话时间线。Agent 气泡上标"谁处理的"（规则解析 / 大模型编排）——
   这是最诚实的做法，也是被追问时一眼能答的证据（spec §7.1）。 */
import { el, clear } from '../core/dom.js';
import { renderSteps } from '../ui/steps.js';

const ENGINE_ZH = { rules: '规则解析', llm: '大模型编排', chat_llm: '大模型编排' };

export function createTimeline({ host, store }) {
  function render(state) {
    clear(host);
    const list = state.timeline || [];
    if (list.length === 0) {
      const intro = el('p', 'timeline__intro', '说一句话就能发起清洗与评估。');
      host.appendChild(intro);
      return;
    }
    for (const item of list) {
      host.appendChild(item.role === 'user' ? userBubble(item) : agentBubble(item));
    }
    host.scrollTop = host.scrollHeight;
  }
  return { render };
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
