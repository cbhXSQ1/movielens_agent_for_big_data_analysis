/* =============================================================================
   llm-settings.js —— 大模型 API 接入（居中弹窗）
   -----------------------------------------------------------------------------
   形态依据 Open Design 的 IBM Carbon 契约（design-systems/ibm/DESIGN.md）：
     · Overlay：`0 2px 6px rgba(0,0,0,0.3)` + dark scrim，用途明确写着
       "Modal dialogs, side panels" —— 阴影是 Carbon 专门留给浮层的，
       所以这里的阴影不是违规，而是契约要求（项目里 .toast 用的是同一个值）
     · 0 圆角、无渐变
     · 时长 200–300ms（craft/animation-discipline.md），并尊重 prefers-reduced-motion
     · 弹窗焦点陷阱是**正确行为**（craft/accessibility-baseline.md），
       Escape / 关闭按钮才解除

   三条纪律（与后端 agent/README.md 对齐）：
     1. 入口**常显**。后端没开总开关时也点得开，但弹窗里如实说明为什么用不了，
        而不是把入口藏起来 —— 藏起来等于不可发现。
     2. 配置只写 localStorage，**不写任何仓库文件**，后端也不落盘（随每次请求带）。
     3. 清空后等价于「从未接过大模型」，之后 100% 走规则解析。
   ========================================================================== */

(function (global) {
  'use strict';

  var KEY = 'mlgov.llm';    // 与 app.js 的 mlgov.railWidth / mlgov.chatHeight 同一命名空间
  var $ = function (id) { return document.getElementById(id); };

  var cfg = { enabled: false, api_base: '', api_key: '', model: '', mode: 'fallback' };
  var supported = null;     // null = 还不知道（/health 未返回）；false = 后端总开关没开
  var isOpen = false;
  var lastFocus = null;

  function modal() { return $('settings-modal'); }

  function setStatus(msg) {
    var el = $('llm-status');
    if (el) el.textContent = msg || '';
  }

  function modeFromForm() {
    var el = document.querySelector('input[name="llm-mode"]:checked');
    return el ? el.value : 'fallback';
  }

  /* ------------------------------------------------------------ 配置读写 */

  function load() {
    try {
      var raw = global.localStorage.getItem(KEY);
      if (!raw) return null;
      var o = JSON.parse(raw);
      if (!o || typeof o !== 'object') return null;
      cfg.enabled = !!o.enabled;
      cfg.api_base = o.api_base || '';
      cfg.api_key = o.api_key || '';
      cfg.model = o.model || '';
      cfg.mode = o.mode || 'fallback';
      return cfg;
    } catch (e) { return null; }
  }

  function save() {
    try { global.localStorage.setItem(KEY, JSON.stringify(cfg)); return true; }
    catch (e) { setStatus('本机无法保存（浏览器限制），本次会话内仍然生效。'); return false; }
  }

  function fillForm() {
    if (!$('llm-enabled')) return;
    $('llm-enabled').checked = cfg.enabled;
    $('llm-base').value = cfg.api_base;
    $('llm-key').value = cfg.api_key;
    $('llm-model').value = cfg.model;
    var radio = document.querySelector('input[name="llm-mode"][value="' + cfg.mode + '"]');
    if (radio) radio.checked = true;
  }

  function readForm() {
    cfg.enabled = $('llm-enabled').checked;
    cfg.api_base = ($('llm-base').value || '').trim();
    cfg.api_key = ($('llm-key').value || '').trim();
    cfg.model = ($('llm-model').value || '').trim();
    cfg.mode = modeFromForm();
    return cfg;
  }

  /**
   * 供 app.js 在每次 chat 时取用。
   * 未启用、或三项信息不全，一律返回 null —— 宁可不带，也不带一份半吊子配置。
   */
  function payload() {
    if (!cfg || !cfg.enabled) return null;
    if (!cfg.api_base || !cfg.api_key || !cfg.model) return null;
    // mode 必须带上：后端默认 fallback = 只有规则彻底听不懂才问它，
    // 常用说法规则都能识别，现场演示看不出效果。演示请选 always。
    return {
      enabled: true,
      api_base: cfg.api_base,
      api_key: cfg.api_key,
      model: cfg.model,
      mode: cfg.mode
    };
  }

  /* ------------------------------------------------------- 后端支持与否 */

  /**
   * 由 app.js 的 checkHealth 每次拿到 /health 后调用。
   * 注意：**不隐藏入口**。只根据 supported 决定弹窗里是「可用」还是「如实说明为什么用不了」。
   */
  function syncVisibility(health) {
    var llm = (health && health.llm) || null;
    supported = !!(llm && llm.supported);

    var notice = $('llm-unsupported');
    if (notice) notice.hidden = supported;

    // 不支持时把表单整体禁掉，避免填了半天才发现用不了
    var box = modal();
    if (box) box.classList.toggle('modal--disabled', !supported);
    ['llm-enabled', 'llm-base', 'llm-key', 'llm-model'].forEach(function (id) {
      if ($(id)) $(id).disabled = !supported;
    });
    Array.prototype.forEach.call(document.querySelectorAll('input[name="llm-mode"]'), function (r) {
      r.disabled = !supported;
    });
    ['btn-llm-test', 'btn-llm-save', 'btn-llm-clear'].forEach(function (id) {
      if ($(id)) $(id).disabled = !supported;
    });

    if (isOpen) setStatus(supported
      ? (llm && llm.configured
          ? '后端已配好大模型。是否使用由上面的开关决定。'
          : '后端总开关已开。填好下面三项并保存后，本机的提问才会带上这份配置。')
      : '');
    return supported;
  }

  /* --------------------------------------------------------------- 弹窗 */

  function focusables() {
    var box = modal();
    if (!box) return [];
    return Array.prototype.filter.call(
      box.querySelectorAll('button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])'),
      function (el) { return !el.disabled; });
  }

  function openModal() {
    var box = modal();
    if (!box) return;
    load(); fillForm();
    lastFocus = document.activeElement;
    box.hidden = false;
    isOpen = true;
    document.body.classList.add('is-modal-open');

    setStatus(supported === false
      ? ''
      : (supported
          ? '后端总开关已开。填好下面三项并保存后，本机的提问才会带上这份配置；在此之前 100% 走规则解析。'
          : '正在读取后端状态…'));

    var close = $('btn-settings-close');
    if (close) close.focus();
  }

  function closeModal() {
    var box = modal();
    if (!box || !isOpen) return;
    box.hidden = true;
    isOpen = false;
    document.body.classList.remove('is-modal-open');

    // 焦点还给触发它的按钮，不然键盘用户关掉弹窗后会掉回页面开头。
    // 注意：程序化 click() 不会聚焦按钮（此时 activeElement 是 body），
    // 所以要先判断它是不是真的可聚焦，否则直接回退到入口按钮。
    var back = (lastFocus && lastFocus !== document.body &&
                lastFocus !== document.documentElement && lastFocus.focus)
      ? lastFocus
      : $('btn-settings');
    if (back && back.focus) back.focus();
  }

  function bind() {
    $('btn-settings').addEventListener('click', openModal);
    $('btn-settings-close').addEventListener('click', closeModal);

    // 点遮罩关闭（弹窗本体上的点击不冒泡到这里）
    var scrim = $('settings-scrim');
    if (scrim) scrim.addEventListener('click', closeModal);

    // ESC 关闭 + Tab 焦点陷阱
    // （craft/accessibility-baseline.md：弹窗困住焦点是设计使然，不是违规）
    document.addEventListener('keydown', function (e) {
      if (!isOpen) return;
      if (e.key === 'Escape') { e.preventDefault(); closeModal(); return; }
      if (e.key !== 'Tab') return;
      var list = focusables();
      if (!list.length) return;
      var first = list[0], last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    $('btn-llm-save').addEventListener('click', function () {
      readForm();
      if (cfg.enabled && (!cfg.api_base || !cfg.api_key || !cfg.model)) {
        setStatus('三个字段都要填齐；不全的话本次不会启用，仍然走规则解析。');
        return;
      }
      save();
      setStatus(cfg.enabled
        ? '已保存并启用。之后的提问会带上这份配置（只对本机生效），解析来源会在对话气泡上标出。'
        : '已保存为关闭。之后的提问 100% 走规则解析。');
    });

    $('btn-llm-clear').addEventListener('click', function () {
      try { global.localStorage.removeItem(KEY); } catch (e) { /* 忽略 */ }
      cfg = { enabled: false, api_base: '', api_key: '', model: '', mode: 'fallback' };
      fillForm();
      setStatus('已清除本机保存，等同于「从未接过大模型」。');
    });

    $('btn-llm-test').addEventListener('click', function () {
      readForm();
      var p = payload();
      if (!p) { setStatus('请先勾选启用，并把接口地址 / API Key / 模型名都填齐，再测试。'); return; }
      setStatus('正在测试连接…');
      global.ODAPI.testLLM(p).then(function (env) {
        if (!env || !env.ok || !env.reachable) {
          var info = global.ODAPI.describeError(env && env.error);
          setStatus('连不上：' + (info.text || '调用失败') +
            (info.message ? '（' + info.message + '）' : '') +
            ' —— 本次按规则解析处理。');
          return;
        }
        var m = (env.config && env.config.model) || p.model;
        setStatus('连通成功：' + env.latency_ms + ' ms，模型 ' + m + '。');
      });
    });
  }

  global.ODLLM = {
    payload: payload,
    syncVisibility: syncVisibility,
    open: openModal,
    close: closeModal,
    init: function () {
      if (!$('btn-settings') || !modal()) return;
      bind(); load(); fillForm();
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { global.ODLLM.init(); });
  } else {
    global.ODLLM.init();
  }
})(window);
