/* 大模型接入设置（spec §3.1 顶栏「设置」）。纪律沿用旧实现：
   入口常显（后端没开也点得开）、配置只写 localStorage['mlgov.llm']、
   打开即锁 body 滚动、焦点陷阱、Esc 关闭并把焦点还给入口按钮。 */
import { el, clear } from '../core/dom.js';

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

export function createSettings({ modal, store, api, opener }) {
  let lastFocus = null;

  function close() {
    modal.hidden = true;
    modal.textContent = '';
    document.body.classList.remove('is-modal-open');
    if (lastFocus) lastFocus.focus();
  }

  function open() {
    /* brief 的签名里就有 opener，但给的代码只记 document.activeElement；
       鼠标点击在某些浏览器不会把焦点给按钮，这里优先用入口元素，
       保证 Esc 之后焦点一定回到「设置」。 */
    lastFocus = opener || document.activeElement;
    const cfg = store.get().llm;
    const health = store.get().health;
    clear(modal);
    modal.hidden = false;
    document.body.classList.add('is-modal-open');

    const scrim = el('div', 'modal__scrim');
    scrim.addEventListener('click', close);
    modal.appendChild(scrim);

    const box = el('div', 'modal__box');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', 'settings-title');

    const head = el('div', 'modal__head');
    const h = el('h2', 'modal__title', '大模型接入'); h.id = 'settings-title';
    head.appendChild(h);
    const x = el('button', 'modal__close', '关闭'); x.type = 'button';
    x.addEventListener('click', close);
    head.appendChild(x);
    box.appendChild(head);

    const body = el('div', 'modal__body');
    if (!health.llmSupported) {
      box.dataset.supported = 'false';
      const warn = el('p', 'notice notice--warn',
        '后端没有开启大模型增强。启动后端时设置 AGENT_LLM_SUPPORTED=1 即可使用。');
      body.appendChild(warn);
    }
    body.appendChild(el('p', 'field__hint',
      '它只负责把话理解成一次工具调用；分数与行数仍然来自 Hadoop 的真实运行。'));

    const fields = [
      ['enabled', '启用大模型增强', 'checkbox'],
      ['api_base', '接口地址（OpenAI 兼容）', 'text'],
      ['api_key', 'API Key', 'password'],
      ['model', '模型名', 'text'],
    ];
    const inputs = {};
    for (const [key, label, type] of fields) {
      const wrap = el('div', 'field');
      const lab = el('label', 'field__label', label);
      lab.htmlFor = 'llm-' + key;
      wrap.appendChild(lab);
      const input = el('input', 'field__input');
      input.id = 'llm-' + key;
      input.type = type;
      if (type === 'checkbox') input.checked = !!cfg[key]; else input.value = cfg[key] || '';
      if (!health.llmSupported) input.disabled = true;
      wrap.appendChild(input);
      inputs[key] = input;
      body.appendChild(wrap);
    }
    box.appendChild(body);

    const foot = el('div', 'modal__foot');
    const status = el('p', 'field__hint'); status.setAttribute('role', 'status');
    const test = el('button', 'btn btn--ghost btn--sm', '测试连接'); test.type = 'button';
    test.addEventListener('click', async () => {
      status.textContent = '正在测试…';
      const res = await api.llmTest(read(inputs));
      status.textContent = res.ok ? `连上了，用时 ${res.data.latency_ms} ms` : res.error.message;
    });
    const save = el('button', 'btn btn--primary btn--sm', '保存'); save.type = 'button';
    save.addEventListener('click', () => {
      const next = read(inputs);
      try { localStorage.setItem('mlgov.llm', JSON.stringify(next)); } catch { /* 忽略 */ }
      store.set({ llm: next });
      close();
    });
    foot.appendChild(status); foot.appendChild(test); foot.appendChild(save);
    box.appendChild(foot);

    modal.appendChild(box);

    /* 焦点陷阱 */
    const focusables = () => [...box.querySelectorAll(FOCUSABLE)].filter(n => !n.disabled);
    box.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (e.key !== 'Tab') return;
      const list = focusables();
      if (list.length === 0) return;
      const first = list[0], last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    (focusables()[0] || box).focus();
  }

  function read(inputs) {
    return {
      enabled: inputs.enabled.checked,
      api_base: inputs.api_base.value.trim(),
      api_key: inputs.api_key.value,
      model: inputs.model.value.trim(),
      mode: 'on',
    };
  }

  return {
    start(button) {
      button.addEventListener('click', open);
    },
  };
}
