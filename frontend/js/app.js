/* =============================================================================
   app.js —— 控制台主逻辑：一次提示 → 轮询 → 渲染
   -----------------------------------------------------------------------------
   三条红线（作业 §5「保证结果可信」，违反即失分）：
     1. 不编造：任务未成功时，结果区一个数字都不显示。绝不拿上一轮旧数字顶替。
     2. 隔离 ≠ 修复：展示分数时同屏展示数据量变化与隔离数（本页做成常驻提示条）。
     3. 配置只读：规则与阈值全部来自 config/*.json 与接口返回，前端不硬编码。

   状态覆盖（craft/state-coverage.md 的硬要求）：
     每个面板都要有 加载 / 空 / 错误 / 有数据 / 极端值 五种形态。
   ========================================================================== */

(function (global) {
  'use strict';

  var API = global.ODAPI;
  var FMT = global.ODFmt;
  var CH = global.ODCharts;
  var SC = global.ODScoring;
  var $ = function (id) { return document.getElementById(id); };

  /* ------------------------------------------------------------ 常量（UI 词汇） */

  var POLL_MS = 3000;

  /** 阶段序列 → 中文。顺序不硬编码，用接口返回的 stage_total 与阶段名对齐。 */
  var STAGE_ZH = {
    queued: '排队中', running: '执行中',
    clean_users: '用户表清洗', clean_movies: '电影表清洗', clean_ratings: '评分表清洗',
    stats_marks: '统计打标', score_before: '清洗前评分', score_after: '清洗后评分',
    finalize: '汇总', publish: '发布', done: '完成'
  };

  /** 阶段顺序（仅用于画步进器；若某阶段名不在表内，会原样显示而不丢） */
  var STAGE_ORDER = ['clean_users', 'clean_movies', 'clean_ratings', 'stats_marks',
                     'score_before', 'score_after', 'finalize', 'publish', 'done'];

  /** 五维固定英文名 → 中文兜底（配置读不到时用；配置优先） */
  var DIM_ZH = {
    'Accurate': '准确性', 'Complete': '完整性', 'Unique': '唯一性',
    'Up-to-date': '时效性', 'Consistent': '一致性'
  };
  var DIM_ORDER = ['Accurate', 'Complete', 'Unique', 'Up-to-date', 'Consistent'];

  /**
   * 全量基准行数 —— 本文件里**唯一**允许存在的领域常量。
   *
   * 用途：仅用于判断「本次是不是抽样运行」，从而决定要不要挂"数字不可用于汇报"的横幅。
   * **它不参与任何结果展示** —— 页面上所有分数、计数、比例全部来自接口返回。
   *
   * 来源：ml-1m 的已知全量规模，与第 4 节黄金基线 §7.3 的输入行数一致。
   * 为什么必须写死：后端没有提供"全量应有多少行"的接口，
   * 而"这次是不是全量"恰恰是必须由前端主动核对、否则会把样本数字当正式结果的关键判断。
   */
  var FULL_RATINGS_LINES = 1150241;

  /**
   * 左右栏宽度（分隔条可拖动）。
   * BP 必须与 css 里 .splitter 被隐藏的断点（1100px）保持一致，
   * 否则会出现「单栏布局下还记着一个左栏宽度」的错位。
   */
  var SPLIT = {
    MIN: 280,          // 左栏最窄
    MAX: 720,          // 左栏最宽
    MAIN_MIN: 480,     // 右栏至少留这么宽，否则一拖就把结果区压没
    STEP: 16,          // 键盘 ← / → 步长
    STEP_BIG: 48,      // 按住 Shift 的步长
    BP: 1100,          // 与 css 一致
    CHAT_MAX_H: 240,   // 追问输入框长高上限，与 css 的 .field--chat 一致
    KEY: 'mlgov.railWidth'
  };

  /**
   * 追问区高度（横向分隔条）。
   * RESERVE = 主操作栏 + 分隔条 + 上面至少要露出的高度 ——
   * 保证把分隔条拖到顶，也还看得见「发送给 Agent」和一点上下文。
   * FRACTION 必须与 css 的 --chat-h: 36vh 对齐。
   */
  var HSPLIT = {
    MIN: 180,
    RESERVE: 290,
    FRACTION: 0.36,
    STEP: 24,          // 键盘 ↑ / ↓ 步长
    STEP_BIG: 64,      // 按住 Shift 的步长
    KEY: 'mlgov.chatHeight'
  };

  /* ------------------------------------------------------------------ 状态 */

  var state = {
    taskId: null,
    status: null,
    result: null,
    timer: null,
    consecutiveErrors: 0,
    startedAt: null,
    execMode: 'cluster',
    evidence: { type: 'quarantine', table: 'ratings', n: 20 },
    scoringCfg: null,
    cleaningCfg: null,
    configError: null,
    reportMd: null,
    dismissedBanners: {}
  };

  /* ------------------------------------------------------------------ 小工具 */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  function emptyState(host, title, body) {
    clear(host);
    var box = el('div', 'empty');
    box.appendChild(el('p', 'empty__title', title));
    if (body) box.appendChild(el('p', 'empty__body', body));
    host.appendChild(box);
  }

  function errorState(host, title, cause, hint, actionLabel, actionFn) {
    clear(host);
    var box = el('div', 'state-error');
    box.appendChild(el('p', 'state-error__title', title));
    if (cause) box.appendChild(el('p', 'state-error__cause', cause));
    if (hint) box.appendChild(el('p', 'state-error__hint', hint));
    if (actionLabel && actionFn) {
      var row = el('div', 'state-error__action');
      var btn = el('button', 'btn btn--tertiary btn--sm', actionLabel);
      btn.type = 'button';
      btn.addEventListener('click', actionFn);
      row.appendChild(btn);
      box.appendChild(row);
    }
    host.appendChild(box);
  }

  function kvRows(pairs) {
    var dl = el('dl', 'kv');
    pairs.forEach(function (p) {
      if (p[1] === undefined || p[1] === null || p[1] === '') return;
      dl.appendChild(el('dt', null, p[0]));
      var dd = el('dd');
      if (p[2] === 'mono') dd.className = 'mono';
      dd.textContent = String(p[1]);
      dl.appendChild(dd);
    });
    return dl;
  }

  function toast(msg) {
    var t = $('toast');
    t.textContent = msg;
    t.classList.add('toast--visible');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { t.classList.remove('toast--visible'); }, 3200);
  }

  /* ------------------------------------------------------------- 通知条 */

  function banner(key, kind, title, text, onClose) {
    if (state.dismissedBanners[key]) return;
    var stack = $('banner-stack');
    var old = stack.querySelector('[data-banner="' + key + '"]');
    if (old) old.remove();

    var box = el('div', 'banner banner--' + kind);
    box.setAttribute('data-banner', key);
    var body = el('div', 'banner__body');
    body.appendChild(el('span', 'banner__title', title));
    if (text) {
      body.appendChild(document.createTextNode(' '));
      body.appendChild(el('span', 'banner__text', text));
    }
    box.appendChild(body);
    var close = el('button', 'banner__close', '✕');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭此提示');
    close.addEventListener('click', function () {
      state.dismissedBanners[key] = true;
      box.remove();
      if (onClose) onClose();
    });
    box.appendChild(close);
    stack.appendChild(box);
  }

  function removeBanner(key) {
    var stack = $('banner-stack');
    var b = stack.querySelector('[data-banner="' + key + '"]');
    if (b) b.remove();
  }

  /* ------------------------------------------------------------- 页签 */

  function initTabs() {
    var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = (t === tab);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;
        var panel = $(t.getAttribute('aria-controls'));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
    }
    tabs.forEach(function (tab, i) {
      tab.addEventListener('click', function () { select(tab); });
      tab.addEventListener('keydown', function (e) {
        var k = e.key, next = null;
        if (k === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
        else if (k === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (k === 'Home') next = tabs[0];
        else if (k === 'End') next = tabs[tabs.length - 1];
        if (next) { e.preventDefault(); select(next, true); }
      });
    });
  }

  /* ------------------------------------------------------------- 后端状态 */

  function checkHealth() {
    return API.health().then(function (env) {
      var box = $('health');
      var ok = env && env.ok;
      box.classList.toggle('health--ok', !!ok);
      box.classList.toggle('health--bad', !ok);
      box.querySelector('.health__text').textContent = ok ? '后端已连接' : '后端未连接';

      // 大模型入口由后端声明：没开总开关（AGENT_LLM_SUPPORTED）就完全不出现相关 UI
      try {
        if (global.ODLLM && global.ODLLM.syncVisibility) global.ODLLM.syncVisibility(env);
      } catch (e) { /* 面板模块没加载就算了，不影响主流程 */ }

      if (ok) {
        removeBanner('offline');
      } else {
        var msg = env && env.error ? env.error.message : '未知原因';
        banner('offline', 'error', 'Agent 服务未连接。', msg +
          ' 请先运行：python -m agent.http_api --port 8765');
      }
      return !!ok;
    });
  }

  /* ------------------------------------------------------------- 顶栏标签 */

  function setTag(id, label, value) {
    var n = $(id);
    if (!n) return;
    n.textContent = label + ' ' + (value || '—');
  }

  /** 任务成功后用本次任务的实际值覆盖；之前用配置里登记的候选值并标注来源。 */
  function updateHeaderTags(fromResult) {
    if (fromResult) {
      setTag('tag-data-version', '数据版本', fromResult.data_version);
      var tb = fromResult.time_boundaries || {};
      setTag('tag-t1', 'T1', tb.T1);
      setTag('tag-t2', 'T2', tb.T2);
      ['tag-data-version', 'tag-t1', 'tag-t2'].forEach(function (i) {
        $(i).title = '来自本次任务结果';
      });
      return;
    }
    var dv = state.cleaningCfg && state.cleaningCfg.data_version;
    if (dv) {
      setTag('tag-data-version', '数据版本', dv.id);
      var tbv = dv.time_boundaries || {};
      setTag('tag-t1', 'T1', tbv.T1);
      setTag('tag-t2', 'T2', tbv.T2);
      ['tag-data-version', 'tag-t1', 'tag-t2'].forEach(function (i) {
        $(i).title = '来自已登记方案（config/cleaning_rules.v1.json），任务成功后切换为本次任务的实际值';
      });
    }
  }

  /* ------------------------------------------------------------- 进度渲染 */

  function renderProgress() {
    var host = $('progress-body');
    var st = state.status;

    if (!st) {
      emptyState(host, '还没有任务', '发起后在这里看进度。');
      return;
    }

    clear(host);
    var box = el('div', 'progress');

    var statusZh = { queued: '排队中', running: '执行中', succeeded: '已完成', failed: '已失败' }[st.status] || st.status;

    // 头部：阶段 + 百分比
    var head = el('div', 'progress__head');
    head.appendChild(el('span', 'progress__stage', STAGE_ZH[st.stage] || st.stage || statusZh));
    head.appendChild(el('span', 'progress__pct', (st.progress_percent === undefined ? '—' : st.progress_percent + '%')));
    box.appendChild(head);

    // 进度条
    var bar = el('div', 'bar');
    if (st.status === 'succeeded') bar.classList.add('bar--done');
    if (st.status === 'failed') bar.classList.add('bar--failed');
    var fill = el('div', 'bar__fill');
    fill.style.width = Math.max(0, Math.min(100, Number(st.progress_percent) || 0)) + '%';
    bar.appendChild(fill);
    box.appendChild(bar);

    // 元信息
    var meta = el('div', 'progress__meta');
    var total = st.stage_total ? (st.stage_total + ' 步') : '—';
    meta.appendChild(el('span', null, '第 ' + (st.stage_index || '—') + ' / ' + total + ' 步'));
    meta.appendChild(el('span', null, '任务 ' + (st.task_id || state.taskId || '—')));
    if (state.startedAt) {
      var secs = Math.round((Date.now() - state.startedAt) / 1000);
      meta.appendChild(el('span', null, '已用时 ' + fmtDuration(secs)));
    }
    box.appendChild(meta);

    // 步进器
    var steps = el('div', 'steps');
    var ordered = STAGE_ORDER.slice();
    // 接口若是新增了阶段名，追加到末尾，不丢信息
    if (st.stage && ordered.indexOf(st.stage) === -1) ordered.push(st.stage);
    ordered.forEach(function (name, i) {
      var cls = 'step';
      var idx = i + 1;
      if (st.status === 'succeeded') cls += ' step--done';
      else if (st.status === 'failed') {
        if (idx < (st.stage_index || 0)) cls += ' step--done';
        else if (idx === (st.stage_index || 0)) cls += ' step--failed';
      } else if (idx < (st.stage_index || 0)) cls += ' step--done';
      else if (idx === (st.stage_index || 0)) cls += ' step--active';

      var row = el('div', cls);
      row.appendChild(el('span', 'step__dot'));
      row.appendChild(el('span', null, (i + 1) + '. ' + (STAGE_ZH[name] || name)));
      steps.appendChild(row);
    });
    box.appendChild(steps);

    // 长任务说明（顶层任务约 8–10 分钟，属于"比预期久"的正常区间）
    if (st.status === 'running' && state.startedAt) {
      var secs2 = Math.round((Date.now() - state.startedAt) / 1000);
      if (secs2 > 60) {
        var note = el('p', 'field__hint',
          '已运行超过 1 分钟。全量 Hadoop 集群任务通常需要 8–10 分钟，请保持页面打开。');
        box.appendChild(note);
      }
    }

    // 失败态：如实给环节与原因，不给任何结果数字
    if (st.status === 'failed') {
      var errs = Array.isArray(st.errors) ? st.errors : [];
      var first = errs[0] || {};
      var cause = first.message || st.message || '未提供原因';
      // 实测（2026-09-27）：errors[].stage 可能是空串，后端只填了 message；
      // 顶层 st.stage 才是可靠的阶段名，所以要回退。
      var stageName = first.stage || st.stage;
      var where = stageName
        ? ('失败环节：' + (STAGE_ZH[stageName] || stageName) +
           '（第 ' + (st.stage_index || '?') + ' / ' + (st.stage_total || '?') + ' 步）')
        : '失败环节：后端未返回阶段名';
      if (first.job) where += ' · 作业 ' + first.job;
      if (first.exit_code !== undefined && first.exit_code !== null) where += ' · 退出码 ' + first.exit_code;
      errorState(box, '任务执行失败', cause,
        where + '。任务已产出的中间文件仍保留在任务目录，但按接口契约（status=failed）不会返回结果。',
        '重新发起', function () { send(); });
    }

    host.appendChild(box);
  }

  function fmtDuration(sec) {
    if (sec < 60) return sec + ' 秒';
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ' 分 ' + (s < 10 ? '0' : '') + s + ' 秒';
  }

  /* ------------------------------------------------------------- 轮询 */

  function stopPolling() {
    if (state.timer) { clearTimeout(state.timer); state.timer = null; }
  }

  function poll() {
    if (!state.taskId) return;
    API.status(state.taskId).then(function (env) {
      if (env && env.ok) {
        state.consecutiveErrors = 0;
        state.status = env;
        renderProgress();
        if (env.status === 'succeeded') {
          stopPolling();
          onSucceeded();
          return;
        }
        if (env.status === 'failed') {
          stopPolling();
          onFailed(env);
          return;
        }
      } else {
        // 单次查询失败：指数退避 2s / 4s / 8s，三次后如实报错并给重试
        state.consecutiveErrors++;
        if (state.consecutiveErrors >= 3) {
          stopPolling();
          var info = API.describeError(env && env.error);
          errorState($('progress-body'), '查不到任务进度',
            (info.text || '') + (info.message ? '：' + info.message : ''),
            '已连续 3 次查询失败（每次间隔递增）。任务可能仍在后台运行。',
            '重试查询', function () { state.consecutiveErrors = 0; poll(); });
          return;
        }
      }
      state.timer = setTimeout(poll, POLL_MS * Math.pow(2, Math.max(0, state.consecutiveErrors - 1)) + 1000);
    });
  }

  function onSucceeded() {
    toast('任务完成，正在读取结果…');
    loadResult();
  }

  function onFailed(st) {
    var first = (st.errors && st.errors[0]) || {};
    banner('task-failed', 'error', '任务失败。',
      (first.message || st.message || '未提供原因') + ' 五个维度的结果未产出。');
  }

  /* ------------------------------------------------------------- 结果加载 */

  function loadResult() {
    if (!state.taskId) return;
    API.result(state.taskId, true).then(function (env) {
      if (!env || !env.ok) {
        var info = API.describeError(env && env.error);
        state.result = null;
        errorState($('overview-explanation'), '取不到结果',
          (info.text || '') + (info.message ? '：' + info.message : ''),
          info.advice || '');
        return;
      }
      state.result = env;
      renderAll(env);
      refreshSamples();
      checkSampling(env);
    });
  }

  function checkSampling(res) {
    var lines = res && res.counts && res.counts.input ? res.counts.input.ratings_lines : null;
    if (lines === null || lines === undefined) return;

    var full = (Number(lines) === FULL_RATINGS_LINES);

    // 光看行数不够：还要看走没走 Hadoop。
    // 本地引擎跑全量时行数也是 1,150,241，但那批数字没有经过 Hadoop，
    // 不能标成"可用于正式汇报"（作业要求：清洗与评分必须由 Hadoop 实际执行）。
    //
    // 判据优先取「结果自带」的事实，而不是当前选中的执行方式 ——
    // 通过追问接管一个既有任务时，页面上的单选按钮跟那个任务无关。
    // D-015 起：非集群模式的 paths.published_dir 为 null，这是权威判据。
    var viaHadoop;
    var paths = res && res.paths;
    if (paths && Object.prototype.hasOwnProperty.call(paths, 'published_dir')) {
      viaHadoop = (paths.published_dir !== null && paths.published_dir !== undefined);
    } else {
      viaHadoop = (state.execMode !== 'local');   // 兜底：结果没带该字段时按当前选择
    }

    // 每次结果到达都重新提示，不允许被上一次的关闭动作永久压掉
    state.dismissedBanners['run-scope'] = false;

    if (full && viaHadoop) {
      banner('run-scope', 'success', '全量 · Hadoop 集群。',
        '输入评分 ' + FMT.int(lines) + ' 行，与全量基准一致；经 Hadoop Streaming 执行。');
    } else if (full && !viaHadoop) {
      banner('run-scope', 'warning', '全量 · 本地引擎。',
        '输入评分 ' + FMT.int(lines) + ' 行，规模与全量一致；但本次未经 Hadoop 执行。' +
        '需要 Hadoop 口径请用「Hadoop 集群」重跑。');
    } else {
      banner('run-scope', 'warning', '样本 · 非全量。',
        '本次输入评分 ' + FMT.int(lines) + ' 行，全量为 ' + FMT.int(FULL_RATINGS_LINES) + ' 行。');
    }
  }

  /* ------------------------------------------------------------- 总览渲染 */

  /**
   * 逐块渲染，每块单独 try/catch。
   * 理由：任一区块渲染出错都不该让整页空白 —— 演示现场尤其不能"一个 bug 全页崩"。
   * 出错的区块自己显示错误信息，其余区块照常渲染。
   */
  function renderAll(res) {
    updateHeaderTags(res);

    // 结果到了，各视图顶部的空态就该消失（卡片里的空位会被下面的渲染器替换掉）
    Array.prototype.forEach.call(document.querySelectorAll('[data-panel-empty]'), function (n) {
      n.hidden = true;
    });

    var jobs = [
      ['overview-explanation', function () { renderOverviewExplanation(res); }],
      ['overview-task',        function () { renderOverviewTask(res); }],
      ['overview-composite',   function () { renderComposite(res); }],
      ['overview-volumes',     function () { renderVolumes(res, $('overview-volumes')); }],
      ['overview-limitations', function () { renderLimitations(res); }],
      ['scores-radar',         function () { renderRadar(res); }],
      ['scores-delta',         function () { renderDelta(res); }],
      ['scores-metrics',       function () { renderMetrics(res); }],
      ['basis-rule-notes',     function () { renderRuleNotes(res); }],
      ['cleaning-volumes',     function () { renderCleaning(res); }]
    ];

    jobs.forEach(function (job) {
      try {
        job[1]();
      } catch (e) {
        errorState($(job[0]), '这一块渲染失败',
          String((e && e.message) ? e.message : e),
          '请把这条信息反馈给开发者。');
      }
    });
  }

  /** 「Agent 对结果的说明」：优先用接口 ?explain=1 回带的 _explanation。 */
  function renderOverviewExplanation(res) {
    var host = $('overview-explanation');
    clear(host);
    var text = res._explanation;
    if (text) {
      var pre = el('div', 'msg__body');
      pre.style.whiteSpace = 'pre-wrap';
      pre.textContent = text;
      host.appendChild(pre);
      return;
    }
    // 后端没给解释时，只用真实数字拼一段最朴素的陈述，不添加任何判断
    var c = res.counts || {}, s = res.scores || {};
    var lines = [];
    if (c.input && c.output) {
      lines.push('输入：评分 ' + FMT.int(c.input.ratings_lines) + ' / 用户 ' + FMT.int(c.input.users_lines) +
                 ' / 电影 ' + FMT.int(c.input.movies_lines) + ' 行。');
      lines.push('输出：评分 ' + FMT.int(c.output.ratings) + ' / 用户 ' + FMT.int(c.output.users) +
                 ' / 电影 ' + FMT.int(c.output.movies) + ' 行。');
    }
    if (c.quarantine) lines.push('隔离 ' + FMT.int(c.quarantine.total) + ' 行；去重 ' +
      FMT.int((c.dedupe || {}).ratings) + ' 条（评分表）。');
    if (s.before && s.after) lines.push('综合分：' + FMT.num(s.before.composite) + ' → ' + FMT.num(s.after.composite) + '。');
    lines.push('（接口未回带中文解释，以上为按结果字段直述；完整解释见下方「局限性」与报告。）');
    var p = el('div', 'msg__body');
    p.style.whiteSpace = 'pre-wrap';
    p.textContent = lines.join('\n');
    host.appendChild(p);
  }

  function renderOverviewTask(res) {
    var host = $('overview-task');
    clear(host);
    var v = res.versions || {};
    var shortSha = function (o) { return (o && o.sha256) ? o.sha256.slice(0, 12) + '…' : '—'; };
    host.appendChild(kvRows([
      ['任务标识', res.task_id || state.taskId],
      ['状态', res.status || 'succeeded'],
      ['数据版本', res.data_version],
      ['规则版本', (v.rule && v.rule.version) ? (v.rule.version + '  sha256 ' + shortSha(v.rule)) : '—'],
      ['评分方案版本', (v.scoring && v.scoring.version) ? (v.scoring.version + '  sha256 ' + shortSha(v.scoring)) : '—'],
      ['策略指纹', v.policy ? shortSha(v.policy) : '—'],
      ['算子库', v.operator_library],
      ['T1 训练期截止', (res.time_boundaries || {}).T1],
      ['T2 验证期截止', (res.time_boundaries || {}).T2]
    ]));
  }

  function renderComposite(res) {
    var host = $('overview-composite');
    clear(host);
    var s = res.scores || {};
    if (!s.before || !s.after) {
      emptyState(host, '本次没有综合分', '');
      return;
    }
    var box = el('div', 'kpi');
    box.appendChild(el('div', 'kpi__label', '清洗前 → 清洗后'));
    var line = el('div');
    line.appendChild(el('span', 'kpi__value kpi__value--lg', FMT.num(s.before.composite)));
    line.appendChild(document.createTextNode('  →  '));
    line.appendChild(el('span', 'kpi__value kpi__value--lg', FMT.num(s.after.composite)));
    box.appendChild(line);
    var d = s.delta ? s.delta.composite : null;
    box.appendChild(el('div', 'kpi__delta ' + deltaClass(d), '变化 ' + FMT.delta(d)));
    host.appendChild(box);
    host.appendChild(el('p', 'field__hint',
      '综合分只在同一 (规则版本, 评分方案版本) 内可比。本次两侧使用同一份评分方案。'));
  }

  function deltaClass(v) {
    if (v === null || v === undefined || isNaN(v)) return 'kpi__delta--flat';
    if (v > 0) return 'kpi__delta--up';
    if (v < 0) return 'kpi__delta--down';
    return 'kpi__delta--flat';
  }

  /** 数据量变化表（总览与清洗结果共用）。 */
  function renderVolumes(res, host) {
    clear(host);
    var c = res.counts || {};
    if (!c.input || !c.output) {
      emptyState(host, '本次没有数据量信息', '');
      return;
    }
    var rows = [
      { key: 'ratings', label: '评分表', input: c.input.ratings_lines, output: c.output.ratings },
      { key: 'users',   label: '用户表', input: c.input.users_lines,   output: c.output.users },
      { key: 'movies',  label: '电影表', input: c.input.movies_lines,  output: c.output.movies }
    ];
    // 注意：CH.volumeBars() 返回的是 SVG 字符串，必须用 innerHTML 注入；
    // appendChild 只接受 DOM 节点，传字符串会抛 TypeError。
    var chartBox = el('div');
    chartBox.innerHTML = CH.volumeBars({ rows: rows, ariaLabel: '三表输入输出数据量变化' });
    host.appendChild(chartBox);

    var wrap = el('div', 'table-wrap');
    var t = el('table', 'data');
    var thead = el('thead'); var hr = el('tr');
    ['表', '输入行数', '输出行数', '变化'].forEach(function (h, i) {
      var th = el('th', i === 0 ? null : 'num', h); hr.appendChild(th);
    });
    thead.appendChild(hr); t.appendChild(thead);
    var tb = el('tbody');
    rows.forEach(function (r) {
      var diff = (Number(r.output) || 0) - (Number(r.input) || 0);
      var tr = el('tr');
      tr.appendChild(el('td', null, r.label));
      tr.appendChild(el('td', 'num', FMT.int(r.input)));
      tr.appendChild(el('td', 'num', FMT.int(r.output)));
      tr.appendChild(el('td', 'num', (diff > 0 ? '+' : diff < 0 ? '−' : '') + FMT.int(Math.abs(diff))));
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t); host.appendChild(wrap);
  }

  function renderLimitations(res) {
    var host = $('overview-limitations');
    clear(host);
    var list = Array.isArray(res.limitations) ? res.limitations : [];
    if (!list.length) {
      emptyState(host, '本次没有局限性说明', '');
      return;
    }
    var ul = el('ul');
    list.forEach(function (x) { ul.appendChild(el('li', null, x)); });
    host.appendChild(ul);
  }

  /* --------------------------------------------------------- 五维对比渲染 */

  function dimLabel(dim) {
    if (state.scoringCfg && state.scoringCfg.dimensions) {
      for (var i = 0; i < state.scoringCfg.dimensions.length; i++) {
        var d = state.scoringCfg.dimensions[i];
        if (d.id === dim && d.name_zh) return d.name_zh;
      }
    }
    return DIM_ZH[dim] || dim;
  }

  function dimKeys(res) {
    var before = (res.scores || {}).before || {};
    var keys = DIM_ORDER.filter(function (k) { return before[k] !== undefined; });
    // 万一方案里出现顺序外的维度，追加而不丢
    Object.keys(before).forEach(function (k) {
      if (k !== 'composite' && keys.indexOf(k) === -1) keys.push(k);
    });
    return keys;
  }

  function renderRadar(res) {
    var host = $('scores-radar');
    var legend = $('scores-legend');
    clear(host); clear(legend);
    var s = res.scores || {};
    if (!s.before || !s.after) {
      emptyState(host, '本次没有五维得分', '');
      return;
    }
    var keys = dimKeys(res);
    host.innerHTML = CH.radar({
      axes: keys.map(function (k) { return { key: k, label: dimLabel(k) }; }),
      series: [
        { name: '清洗前', color: CH.COLORS.before, fillColor: 'rgba(141,141,141,0.16)',
          values: keys.map(function (k) { return s.before[k]; }) },
        { name: '清洗后', color: CH.COLORS.after, fillColor: 'rgba(36,161,72,0.14)',
          values: keys.map(function (k) { return s.after[k]; }) }
      ],
      ariaLabel: '五维质量得分雷达图，清洗前与清洗后对比'
    });

    [['清洗前', CH.COLORS.before], ['清洗后', CH.COLORS.after]].forEach(function (p) {
      var item = el('span', 'legend__item');
      var sw = el('span', 'legend__swatch');
      sw.style.background = p[1];
      item.appendChild(sw);
      item.appendChild(el('span', null, p[0]));
      legend.appendChild(item);
    });
  }

  function renderDelta(res) {
    var host = $('scores-delta');
    clear(host);
    var s = res.scores || {};
    if (!s.before || !s.after) { emptyState(host, '尚无结果', ''); return; }
    var keys = dimKeys(res);
    host.innerHTML = CH.groupedBars({
      rows: keys.map(function (k) {
        return { label: dimLabel(k), before: s.before[k], after: s.after[k] };
      }),
      ariaLabel: '五个维度清洗前后得分对比'
    });

    var wrap = el('div', 'table-wrap');
    var t = el('table', 'data');
    var thead = el('thead'); var hr = el('tr');
    ['维度', '清洗前', '清洗后', '变化'].forEach(function (h, i) {
      hr.appendChild(el('th', i === 0 ? null : 'num', h));
    });
    thead.appendChild(hr); t.appendChild(thead);
    var tb = el('tbody');
    keys.forEach(function (k) {
      var d = s.delta ? s.delta[k] : ((s.after[k] || 0) - (s.before[k] || 0));
      var tr = el('tr');
      tr.appendChild(el('td', null, dimLabel(k) + ' ' + k));
      tr.appendChild(el('td', 'num', FMT.num(s.before[k])));
      tr.appendChild(el('td', 'num', FMT.num(s.after[k])));
      var td = el('td', 'num', FMT.delta(d));
      td.style.color = d > 0 ? '#0e6027' : (d < 0 ? '#a2191f' : 'inherit');
      tr.appendChild(td);
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t); host.appendChild(wrap);
  }

  /** 18 项指标明细：名称从评分方案里查（前端不硬编码指标名）。 */
  function renderMetrics(res) {
    var host = $('scores-metrics');
    clear(host);
    var s = res.scores || {};
    var before = (s.metrics || {}).before;
    var after = (s.metrics || {}).after;
    if (!before && !after) { emptyState(host, '本次没有指标明细', ''); return; }

    var nameOf = {}, dimOf = {};
    if (state.scoringCfg && state.scoringCfg.dimensions) {
      state.scoringCfg.dimensions.forEach(function (d) {
        (d.metrics || []).forEach(function (m) {
          nameOf[m.id] = m.name || '';
          dimOf[m.id] = d.name_zh || d.id;
        });
      });
    }

    var ids = Object.keys(before || {});
    Object.keys(after || {}).forEach(function (k) { if (ids.indexOf(k) === -1) ids.push(k); });
    // 兜底顺序：先按维度分组，再按指标编号
    ids.sort(function (a, b) { return a < b ? -1 : (a > b ? 1 : 0); });

    var missing = [];
    var wrap = el('div', 'table-wrap');
    var t = el('table', 'data');
    var thead = el('thead'); var hr = el('tr');
    ['指标', '维度', '名称', '清洗前', '清洗后', '变化'].forEach(function (h, i) {
      hr.appendChild(el('th', (i >= 3) ? 'num' : null, h));
    });
    thead.appendChild(hr); t.appendChild(thead);

    var tb = el('tbody');
    ids.forEach(function (id) {
      var b = (before || {})[id];
      var a = (after || {})[id];
      if (b === undefined || a === undefined) missing.push(id);
      var tr = el('tr');
      tr.appendChild(el('td', 'mono', id));
      tr.appendChild(el('td', null, dimOf[id] || '—'));
      tr.appendChild(el('td', null, nameOf[id] || '—'));
      ['before', 'after'].forEach(function (side) {
        var v = side === 'before' ? b : a;
        var td = el('td', 'num');
        if (v === undefined || v === null) {
          td.textContent = '本次未产出';       // 缺失 ≠ 0 分
          td.style.color = '#a2191f';
        } else {
          td.textContent = FMT.num(v);
        }
        tr.appendChild(td);
      });
      var dv = (b !== undefined && a !== undefined) ? (a - b) : null;
      var dtd = el('td', 'num', dv === null ? '—' : FMT.delta(dv));
      dtd.style.color = (dv !== null && dv > 0) ? '#0e6027' : ((dv !== null && dv < 0) ? '#a2191f' : 'inherit');
      tr.appendChild(dtd);
      tb.appendChild(tr);
    });
    t.appendChild(tb); wrap.appendChild(t); host.appendChild(wrap);

    if (missing.length) {
      host.appendChild(el('p', 'field__hint',
        '有 ' + missing.length + ' 个指标本次未产出（' + missing.join('、') +
        '）。这通常是评分方案里该指标被停用，或运行样本不覆盖，不是 0 分。'));
    }
  }

  /* --------------------------------------------------------- 清洗结果渲染 */

  function renderCleaning(res) {
    var c = res.counts || {};

    // 修复 / 去重 / 隔离 三张卡（绝不合并成一个"已处理"数字）
    cardList($('cleaning-fix'), c.fix, '本次没有修复计数');
    cardList($('cleaning-dedupe'), c.dedupe, '本次没有去重计数');
    renderQuarantineCard($('cleaning-quarantine'), c.quarantine);

    // 按规则命中排行
    var host = $('cleaning-rules');
    clear(host);
    var by = (c.quarantine || {}).by_rule;
    if (!by || !Object.keys(by).length) {
      emptyState(host, '本次没有按规则命中', '');
    } else {
      var items = Object.keys(by).map(function (k) { return { id: k, v: by[k] }; });
      items.sort(function (a, b) { return b.v - a.v; });
      var max = items[0].v || 1;
      var box = el('div', 'rank');
      items.forEach(function (it) {
        var row = el('div', 'rank__row');
        row.appendChild(el('span', 'rank__id', it.id));
        var track = el('div', 'rank__bar');
        var fill = el('div', 'rank__fill');
        fill.style.width = Math.round((it.v / max) * 100) + '%';
        track.appendChild(fill);
        row.appendChild(track);
        row.appendChild(el('span', 'rank__val', FMT.int(it.v)));
        box.appendChild(row);
      });
      host.appendChild(box);
      host.appendChild(el('p', 'field__hint',
        '为结算后命中数：按执行顺序，前面规则已处理过的记录不重复计入。'));
    }

    renderVolumes(res, $('cleaning-volumes'));
  }

  function cardList(host, obj, emptyText) {
    clear(host);
    var keys = obj ? Object.keys(obj) : [];
    if (!keys.length) { emptyState(host, emptyText, ''); return; }
    host.appendChild(kvRows(keys.map(function (k) { return [k, FMT.int(obj[k]), 'mono']; })));
  }

  function renderQuarantineCard(host, q) {
    clear(host);
    if (!q) { emptyState(host, '本次没有隔离统计', ''); return; }
    var box = el('div', 'kpi');
    box.appendChild(el('div', 'kpi__label', '隔离总数（移出正表）'));
    box.appendChild(el('div', 'kpi__value', FMT.int(q.total)));
    host.appendChild(box);
    var n = q.by_rule ? Object.keys(q.by_rule).length : 0;
    host.appendChild(el('p', 'card__note', '涉及 ' + n + ' 条规则，见下方「按规则命中」。'));
  }

  /* --------------------------------------------------- rule_notes（可选字段） */

  function renderRuleNotes(res) {
    SC.renderRuleNotes(res ? res.rule_notes : null, $('basis-rule-notes'));
  }

  /* ------------------------------------------------------------- 样例浏览 */

  function refreshSamples() {
    var host = $('evidence-table');
    var inline = $('cleaning-samples');
    if (!state.taskId) {
      emptyState(host, '尚无任务', '');
      emptyState(inline, '尚无结果', '');
      return;
    }
    var ev = state.evidence;
    var loading = el('div');
    loading.appendChild(el('div', 'skeleton skeleton--row'));
    loading.appendChild(el('div', 'skeleton skeleton--row'));
    clear(host); host.appendChild(loading);

    API.samples(state.taskId, ev.type, ev.table, ev.n).then(function (env) {
      if (!env || !env.ok) {
        var info = API.describeError(env && env.error);
        errorState(host, '取不到样例', (info.text || '') + (info.message ? '：' + info.message : ''),
          info.advice || '', '重试', refreshSamples);
        return;
      }
      var table = buildSampleTable(env);
      clear(host); host.appendChild(table);

      // 清洗结果页内嵌的那一块，固定看隔离区/评分表
      if (inline) {
        if (ev.type === 'quarantine' && ev.table === 'ratings') {
          clear(inline);
          inline.appendChild(buildSampleTable(env).cloneNode(true));
        } else {
          API.samples(state.taskId, 'quarantine', 'ratings', 8).then(function (e2) {
            clear(inline);
            if (e2 && e2.ok) inline.appendChild(buildSampleTable(e2));
            else emptyState(inline, '取不到样例', '');
          });
        }
      }
    });
  }

  /** 隔离区与清洗后的字段名不同（实测）：隔离有 line_no/rule_id/reason，清洗后只有 line。 */
  function buildSampleTable(env) {
    var rows = env.samples || [];
    var wrap = el('div');

    var head = el('p', 'field__hint');
    head.textContent = '类型 ' + (env.type === 'quarantine' ? '隔离区' : '清洗后') +
      ' · 表 ' + env.table + ' · 共 ' + FMT.int(env.total_available) + ' 条可用，本次取 ' + rows.length + ' 条。';
    wrap.appendChild(head);

    if (!rows.length) {
      var e = el('div', 'empty');
      e.appendChild(el('p', 'empty__title', '没有可展示的记录'));
      e.appendChild(el('p', 'empty__body', '该类型/表当前为空。'));
      wrap.appendChild(e);
      return wrap;
    }

    var isQ = env.type === 'quarantine';
    var tw = el('div', 'table-wrap');
    var t = el('table', 'data');
    var thead = el('thead'); var hr = el('tr');
    var cols = isQ
      ? ['行号', '原始记录', '规则', '阶段', '原因']
      : ['行号', '记录'];
    cols.forEach(function (c, i) { hr.appendChild(el('th', i === 1 ? null : 'num', c)); });
    thead.appendChild(hr); t.appendChild(thead);

    var tb = el('tbody');
    rows.forEach(function (r) {
      var tr = el('tr');
      if (isQ) {
        tr.appendChild(el('td', 'num', r.line_no === undefined ? '—' : r.line_no));
        tr.appendChild(el('td', 'raw', r.raw_line === undefined ? '' : r.raw_line));
        tr.appendChild(el('td', 'mono', r.rule_id || '—'));
        tr.appendChild(el('td', null, r.stage || '—'));
        tr.appendChild(el('td', null, r.reason || '—'));
      } else {
        // 清洗后样例的字段是 line，不是 line_no —— 两种都兜住
        var ln = (r.line_no !== undefined) ? r.line_no : r.line;
        tr.appendChild(el('td', 'num', ln === undefined ? '—' : ln));
        tr.appendChild(el('td', 'raw', r.raw_line === undefined ? '' : r.raw_line));
      }
      tb.appendChild(tr);
    });
    t.appendChild(tb); tw.appendChild(t); wrap.appendChild(tw);
    return wrap;
  }

  /* ------------------------------------------------------------- 报告 */

  function loadReport() {
    if (!state.taskId) { toast('还没有任务'); return; }
    var host = $('report-view');
    clear(host);
    host.appendChild(el('div', 'skeleton skeleton--chart'));
    API.reportMarkdown(state.taskId).then(function (env) {
      if (!env || !env.ok) {
        var info = API.describeError(env && env.error);
        errorState(host, '取不到报告',
          (info.text || '') + (info.message ? '：' + info.message : ''), info.advice || '',
          '重试', loadReport);
        return;
      }
      state.reportMd = env.report || '';
      clear(host);
      var box = el('div', 'report');
      box.innerHTML = renderMarkdown(state.reportMd);
      host.appendChild(box);
      $('btn-copy-report').disabled = false;
      $('btn-download-report').disabled = false;
    });
  }

  /**
   * 极简 Markdown 渲染：只支持标题 / 列表 / 表格 / 粗体 / 行内码 / 代码块 / 分隔线。
   * 不引第三方库（离线环境）。先转义再替换，避免 HTML 注入。
   */
  function renderMarkdown(md) {
    var lines = String(md).replace(/\r\n/g, '\n').split('\n');
    var out = [], i = 0, inCode = false, inList = false, inTable = false;

    function closeList() { if (inList) { out.push('</ul>'); inList = false; } }
    function closeTable() { if (inTable) { out.push('</tbody></table>'); inTable = false; } }

    function inline(s) {
      return CH.esc(s)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    }

    while (i < lines.length) {
      var line = lines[i];

      if (/^```/.test(line)) {
        if (!inCode) { closeList(); closeTable(); out.push('<pre>'); inCode = true; }
        else { out.push('</pre>'); inCode = false; }
        i++; continue;
      }
      if (inCode) { out.push(CH.esc(line)); i++; continue; }

      if (/^\s*$/.test(line)) { closeList(); closeTable(); i++; continue; }

      var h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        closeList(); closeTable();
        var lvl = Math.min(h[1].length, 3);
        out.push('<h' + lvl + '>' + inline(h[2]) + '</h' + lvl + '>');
        i++; continue;
      }

      if (/^(-{3,}|\*{3,})\s*$/.test(line)) { closeList(); closeTable(); out.push('<hr>'); i++; continue; }

      if (/^\s*[-*+]\s+/.test(line)) {
        closeTable();
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push('<li>' + inline(line.replace(/^\s*[-*+]\s+/, '')) + '</li>');
        i++; continue;
      }

      if (/^\s*\|.*\|\s*$/.test(line)) {
        closeList();
        var cells = line.trim().replace(/^\||\|$/g, '').split('|').map(function (c) { return c.trim(); });
        var isSep = cells.every(function (c) { return /^:?-{2,}:?$/.test(c); });
        if (isSep) { i++; continue; }
        if (!inTable) {
          out.push('<table><thead><tr>');
          cells.forEach(function (c) { out.push('<th>' + inline(c) + '</th>'); });
          out.push('</tr></thead><tbody>');
          inTable = true;
        } else {
          out.push('<tr>');
          cells.forEach(function (c) {
            var cls = /^[\d,.\s%+−-]+$/.test(c) ? ' class="num"' : '';
            out.push('<td' + cls + '>' + inline(c) + '</td>');
          });
          out.push('</tr>');
        }
        i++; continue;
      }

      closeList(); closeTable();
      out.push('<p>' + inline(line) + '</p>');
      i++;
    }
    closeList(); closeTable();
    if (inCode) out.push('</pre>');
    return out.join('\n');
  }

  /* ------------------------------------------------------------- 发起任务 */

  function currentExecMode() {
    var checked = document.querySelector('input[name="exec-mode"]:checked');
    return checked ? checked.value : 'cluster';
  }

  /** 执行方式的中文名，界面与对话里统一用这一处。 */
  function modeLabelOf(mode) {
    return mode === 'local' ? '本地引擎' : 'Hadoop 集群';
  }

  /**
   * 大模型面板是可选模块（js/llm-settings.js）：
   * 后端没开总开关时那个脚本什么也不做，这里必须安全地返回 null。
   */
  function currentLlm() {
    try {
      return (global.ODLLM && global.ODLLM.payload) ? global.ODLLM.payload() : null;
    } catch (e) { return null; }
  }

  /**
   * 后端是否**真的**发起了任务。
   * task_started 是后端 01c7cf7 起如实回传的字段，比按 intent 猜可靠；
   * 万一连的是旧后端（没这个字段），退回按意图判断。
   */
  function taskStarted(env) {
    if (env && typeof env.task_started === 'boolean') return env.task_started;
    return !!(env && env.intent === 'clean_evaluate' && env.task_id);
  }

  function send(textOverride) {
    var input = $('prompt');
    var text = (textOverride || input.value || '').trim();
    if (!text) {
      input.setAttribute('aria-invalid', 'true');
      toast('请先输入一句需求');
      input.focus();
      return;
    }
    input.removeAttribute('aria-invalid');

    var btn = $('btn-send');
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');

    state.execMode = currentExecMode();
    state.dismissedBanners['task-failed'] = false;
    removeBanner('task-failed');

    // scope 传 null：交给后端按 task_id 的口径快照补齐，保证前后两次口径一致
    API.chat(text, state.taskId, state.execMode, null, currentLlm()).then(function (env) {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');

      pushChat('user', text, null);

      if (!env || !env.ok) {
        // R20：业务失败时后端把人话说明写在 reply 里（error 字段可能为空），
        // 优先展示 reply，别把说明吞掉只剩「未知错误」。
        if (env && env.reply) {
          pushChat('agent', env.reply, null, true);
        } else {
          var info = API.describeError(env && env.error);
          pushChat('agent', (info.text || '调用失败') + (info.message ? '：' + info.message : '') +
            (info.advice ? '\n' + info.advice : ''), null, true);
        }
        // 发起类失败用顶部横幅再强调一次（如"已有任务在运行"）
        if (env && env.error && env.error.code === 'TASK_ALREADY_RUNNING') {
          banner('busy', 'warning', '已有任务在运行。', '等它跑完再发起，或查看它当前的结果。');
        }
        return;
      }

      pushChat('agent', env.reply || '（Agent 没有返回文字）', env.intent_cn, !env.ok);

      // 后端如实告诉我们「这次真的发起了任务」才进入轮询（比按 intent 猜可靠）
      if (taskStarted(env) && env.task_id) {
        beginTask(env.task_id);
      } else if (env.task_id) {
        // 其它意图带回了任务标识：接管它（刷新页面后靠这条恢复现场）
        adoptTask(env.task_id);
      }

      // quick_demo 不经 Hadoop，必须在界面上说清楚
      if (env.intent === 'quick_demo') {
        banner('quick-demo', 'warning', '这是快速预演，不是 Hadoop 结果。',
          '该路径直接调用本地引擎，不走 HDFS/YARN，不维护任务状态。');
      }
    });
  }

  /**
   * 接管一个「不是我这次点发起」的任务。
   * 场景：用户刷新页面后问「跑到哪一步了」，Agent 会带回最近的任务标识；
   * 此时页面应当恢复现场（进度 / 结果 / 失败原因），而不是只回一句话。
   */
  function adoptTask(taskId) {
    if (!taskId || taskId === state.taskId) return;
    state.taskId = taskId;
    state.result = null;
    state.status = null;
    state.consecutiveErrors = 0;

    API.status(taskId).then(function (env) {
      if (!env || !env.ok) return;          // 拿不到状态就保持空态，不猜
      state.status = env;
      if (env.started_at) {
        var t = Date.parse(env.started_at);
        state.startedAt = isNaN(t) ? null : t;
      }
      renderProgress();
      if (env.status === 'succeeded') { stopPolling(); loadResult(); }
      else if (env.status === 'failed') { stopPolling(); onFailed(env); }
      else { stopPolling(); state.timer = setTimeout(poll, 800); }
    });
  }

  function beginTask(taskId) {
    state.taskId = taskId;
    state.startedAt = Date.now();
    state.status = null;
    state.result = null;
    state.consecutiveErrors = 0;
    renderProgress();
    stopPolling();
    poll();
    // 放在最后：即使这行出问题，轮询也已经发出去了，不会把任务卡死
    scrollRailToProgress();
  }

  /**
   * 发起任务后把左栏上区滚到「执行情况」。
   * 重构后上区是独立滚动区，进度默认落在输入区下面 ——
   * 不滚的话演示时要手动拖一下才看得到 9 个阶段。
   * 用两个 rect 的差值算位置，不用 offsetTop（offsetParent 不确定，算错会跳）。
   */
  function scrollRailToProgress() {
    var box = document.querySelector('.rail__scroll');
    var pb = document.querySelector('[data-od-id="progress-block"]');
    if (!box || !pb) return;
    if (box.scrollHeight <= box.clientHeight) return;          // 本来就没得滚，别动它
    var delta = pb.getBoundingClientRect().top - box.getBoundingClientRect().top;
    box.scrollTop = Math.max(0, box.scrollTop + delta - 8);
  }

  /**
   * 追问。走的是 /api/chat，与主输入框同一个接口。
   *
   * exec_mode 必须带上：接口缺省是 cluster（docs/agent/前端对接接口.md §二）。
   * 这里漏传过一次 —— 用户在左侧选了「本地引擎」，却在追问框里发起清洗，
   * 实际起的是集群任务（本机没装 Hadoop 当场失败；虚拟机里要等约 8 分钟）。
   * 所以不只传参，还把「本次按哪种方式执行」回显到对话里，让执行方式永远看得见。
   */
  function sendChat() {
    var input = $('chat-input');
    var text = (input.value || '').trim();
    if (!text) return;
    input.value = '';
    autoGrowChatInput();

    state.execMode = currentExecMode();

    pushChat('user', text, null);

    // scope 传 null：让后端按 task_id 的口径快照补齐，追问与第一轮口径一致
    API.chat(text, state.taskId, state.execMode, null, currentLlm()).then(function (env) {
      if (!env || !env.ok) {
        var info = API.describeError(env && env.error);
        pushChat('agent', (info.text || '调用失败') + (info.message ? '：' + info.message : ''), null, true);
        if (env && env.error && env.error.code === 'TASK_ALREADY_RUNNING') {
          banner('busy', 'warning', '已有任务在运行。', '等它跑完再发起，或查看它当前的结果。');
        }
        return;
      }
      pushChat('agent', env.reply || '（Agent 没有返回文字）', env.intent_cn, false);

      // 只有真的又发起了一次任务才提执行方式；纯追问不提，免得刷屏。
      // 用后端回显的 opts（**真实生效**的口径）而不是页面上选中的值 —— 两者可能不同
      if (taskStarted(env) && env.task_id) {
        var eff = (env.opts && env.opts.exec_mode) || state.execMode;
        pushChat('agent', '本次按「' + modeLabelOf(eff) + '」执行。', null);
      }
      if (env.task_id) adoptTask(env.task_id);
    });
  }

  /** 追问输入框随内容长高，到上限后自己滚动 */
  function autoGrowChatInput() {
    var t = $('chat-input');
    if (!t || t.tagName !== 'TEXTAREA') return;
    t.style.height = 'auto';
    var full = t.scrollHeight;
    t.style.height = Math.min(full, SPLIT.CHAT_MAX_H) + 'px';
    t.style.overflowY = (full > SPLIT.CHAT_MAX_H) ? 'auto' : 'hidden';
  }

  function pushChat(who, text, intentCn, isError) {
    var log = $('chat-log');
    var intro = log.querySelector('.chat__intro');
    if (intro) intro.remove();

    var msg = el('div', 'msg msg--' + (who === 'user' ? 'user' : 'agent'));
    var meta = el('div', 'msg__meta');
    meta.textContent = who === 'user' ? '你' : 'Agent';
    msg.appendChild(meta);

    if (intentCn) {
      var line = el('div', 'msg__intent');
      line.appendChild(document.createTextNode('识别为：'));
      line.appendChild(el('code', null, intentCn));
      msg.appendChild(line);
    }

    var body = el('div', 'msg__body', text);
    if (isError) body.style.color = '#a2191f';
    msg.appendChild(body);

    log.appendChild(msg);
    log.scrollTop = log.scrollHeight;
  }

  /* ------------------------------------------------------------- 初始化 */

  function initConfig() {
    SC.load().then(function (c) {
      state.scoringCfg = c.scoring;
      state.cleaningCfg = c.cleaning;
      try {
        SC.renderDimensions(c.scoring, $('basis-dimensions'));
        SC.renderRules(c.cleaning, $('basis-rules'));
      } catch (e) {
        $('basis-dimensions').textContent = '';
        $('basis-dimensions').appendChild(SC.errorState(e));
      }
      updateHeaderTags(null);
      // 指标名依赖评分方案，若结果先到就重画一次
      if (state.result) renderMetrics(state.result);
    }).catch(function (err) {
      state.configError = err;
      $('basis-dimensions').textContent = '';
      $('basis-dimensions').appendChild(SC.errorState(err));
      $('basis-rules').textContent = '';
      $('basis-rules').appendChild(SC.errorState(err));
    });
  }

  function initEvents() {
    $('btn-send').addEventListener('click', function () { send(); });

    $('prompt').addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); send(); }
    });

    Array.prototype.forEach.call(document.querySelectorAll('.chip'), function (chip) {
      chip.addEventListener('click', function () {
        $('prompt').value = chip.getAttribute('data-fill') || '';
        $('prompt').focus();
      });
    });

    $('chat-form').addEventListener('submit', function (e) {
      e.preventDefault();
      sendChat();
    });

    // 发送键与上方主输入框统一：Ctrl / Cmd + Enter 发送，Enter 正常换行
    $('chat-input').addEventListener('keydown', function (e) {
      if (!(e.ctrlKey || e.metaKey) || e.key !== 'Enter') return;
      e.preventDefault();
      sendChat();
    });
    $('chat-input').addEventListener('input', autoGrowChatInput);

    $('btn-reload-samples').addEventListener('click', refreshSamples);
    $('btn-load-report').addEventListener('click', loadReport);

    $('btn-copy-report').addEventListener('click', function () {
      if (!state.reportMd) return;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(state.reportMd).then(function () { toast('报告已复制'); },
          function () { toast('复制失败，请手动选择文本'); });
      } else {
        toast('当前浏览器不支持剪贴板接口');
      }
    });

    $('btn-download-report').addEventListener('click', function () {
      if (!state.reportMd) return;
      var blob = new Blob([state.reportMd], { type: 'text/markdown;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'movielens-report-' + (state.taskId || 'task') + '.md';
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast('已开始下载');
    });

    Array.prototype.forEach.call(document.querySelectorAll('#evidence-controls .seg'), function (seg) {
      seg.addEventListener('click', function () {
        var kind = seg.getAttribute('data-kind');
        var value = seg.getAttribute('data-value');
        state.evidence[kind] = (kind === 'n') ? parseInt(value, 10) : value;
        Array.prototype.forEach.call(
          document.querySelectorAll('#evidence-controls .seg[data-kind="' + kind + '"]'),
          function (sib) { sib.setAttribute('aria-pressed', sib === seg ? 'true' : 'false'); });
        refreshSamples();
      });
    });
  }

  /* ------------------------------------------- 左右栏宽度（分隔条可拖动） */

  function railWidthNow() {
    var rail = document.querySelector('.rail');
    return rail ? Math.round(rail.getBoundingClientRect().width) : 400;
  }

  /** 右栏至少保留 MAIN_MIN，否则一拖就把结果区压没了 */
  function railMaxWidth() {
    return Math.max(SPLIT.MIN, Math.min(SPLIT.MAX, window.innerWidth - SPLIT.MAIN_MIN));
  }

  /** 分隔条只在宽屏可用 —— 断点必须与 css 里 .splitter 的 display:none 一致 */
  function splitterUsable() { return window.innerWidth > SPLIT.BP; }

  function setSplitterAria(now) {
    var sp = $('splitter');
    if (!sp || !splitterUsable()) return;   // 单栏时它已被隐藏，报无意义的值只会误导
    sp.setAttribute('aria-valuemin', String(SPLIT.MIN));
    sp.setAttribute('aria-valuemax', String(railMaxWidth()));
    sp.setAttribute('aria-valuenow', String(now === undefined ? railWidthNow() : now));
  }

  function applyRailWidth(px) {
    if (px === null || px === undefined || isNaN(px)) return;
    var w = Math.round(Math.min(railMaxWidth(), Math.max(SPLIT.MIN, px)));
    document.documentElement.style.setProperty('--rail-w', w + 'px');
    setSplitterAria(w);      // 直接用算好的值，不在拖动热路径上再读一次布局
  }

  function saveRailWidth() {
    try { localStorage.setItem(SPLIT.KEY, String(railWidthNow())); }
    catch (e) { /* 隐私模式 / 禁用存储：不记也行，不影响功能 */ }
  }

  function savedRailWidth() {
    try {
      var v = parseInt(localStorage.getItem(SPLIT.KEY), 10);
      return isNaN(v) ? null : v;
    } catch (e) { return null; }
  }

  function resetRailWidth() {
    try { localStorage.removeItem(SPLIT.KEY); } catch (e) { /* 同上 */ }
    document.documentElement.style.removeProperty('--rail-w');   // 交回 CSS，含响应式默认值
    setSplitterAria();
    toast('左右栏宽度已恢复默认');
  }

  /**
   * 分隔条：鼠标 / 触屏拖动 + 键盘。
   * 键盘不是可选项 —— 只做拖动的话，键盘用户完全够不着这个功能。
   */
  function initSplitter() {
    var sp = $('splitter');
    if (!sp) return;

    var saved = savedRailWidth();
    if (saved !== null && splitterUsable()) applyRailWidth(saved);
    else setSplitterAria();

    var dragging = false;

    sp.addEventListener('pointerdown', function (e) {
      if (!splitterUsable()) return;                             // 与 CSS 断点对齐：隐藏时不响应
      if (e.pointerType === 'mouse' && e.button !== 0) return;   // 只响应左键
      dragging = true;
      sp.classList.add('splitter--active');
      document.body.classList.add('is-resizing');
      if (sp.setPointerCapture) {
        try { sp.setPointerCapture(e.pointerId); } catch (err) { /* 忽略：退化为 document 监听 */ }
      }
      e.preventDefault();
    });

    document.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      // 左栏从视口左缘开始，所以指针位置减去分隔条一半宽就是左栏宽度
      applyRailWidth(e.clientX - sp.getBoundingClientRect().width / 2);
      e.preventDefault();
    });

    function endDrag() {
      if (!dragging) return;
      dragging = false;
      sp.classList.remove('splitter--active');
      document.body.classList.remove('is-resizing');
      saveRailWidth();
    }
    document.addEventListener('pointerup', endDrag);
    document.addEventListener('pointercancel', endDrag);

    sp.addEventListener('dblclick', function (e) { e.preventDefault(); resetRailWidth(); });

    sp.addEventListener('keydown', function (e) {
      if (!splitterUsable()) return;   // 单栏模式：分隔条已隐藏，键盘也不该改尺寸
      var k = e.key;
      var step = e.shiftKey ? SPLIT.STEP_BIG : SPLIT.STEP;
      if (k === 'ArrowLeft')       applyRailWidth(railWidthNow() - step);
      else if (k === 'ArrowRight') applyRailWidth(railWidthNow() + step);
      else if (k === 'Home')       applyRailWidth(SPLIT.MIN);
      else if (k === 'End')        applyRailWidth(railMaxWidth());
      else if (k === 'Enter' || k === ' ') { resetRailWidth(); e.preventDefault(); return; }
      else return;
      saveRailWidth();
      e.preventDefault();
    });

    // 窗口尺寸变化时重新处理：变窄就交回 CSS，变宽则恢复记录过的宽度
    window.addEventListener('resize', function () {
      if (!splitterUsable()) {
        document.documentElement.style.removeProperty('--rail-w');
        return;                       // 单栏布局下 --rail-w 根本用不到，别留残留值
      }
      var saved = savedRailWidth();
      if (saved !== null) {
        applyRailWidth(saved);
      } else {
        document.documentElement.style.removeProperty('--rail-w');
        setSplitterAria();
      }
    });
  }

  /* ------------------------------------------- 追问区高度（横向分隔条） */

  function railHeightNow() {
    var rail = document.querySelector('.rail');
    return rail ? Math.round(rail.getBoundingClientRect().height) : 800;
  }

  function chatHeightNow() {
    var blk = document.querySelector('.rail__block--chat');
    return blk ? Math.round(blk.getBoundingClientRect().height) : 0;
  }

  /** 上限：留够 RESERVE，拖到顶也不会把「发送给 Agent」顶出视野 */
  function chatMaxHeight() {
    return Math.max(HSPLIT.MIN, railHeightNow() - HSPLIT.RESERVE);
  }

  function setHSplitterAria(now) {
    var sp = $('splitter-h');
    if (!sp || !splitterUsable()) return;
    sp.setAttribute('aria-valuemin', String(HSPLIT.MIN));
    sp.setAttribute('aria-valuemax', String(chatMaxHeight()));
    sp.setAttribute('aria-valuenow', String(now === undefined ? chatHeightNow() : now));
  }

  function applyChatHeight(px) {
    if (px === null || px === undefined || isNaN(px)) return;
    var h = Math.round(Math.min(chatMaxHeight(), Math.max(HSPLIT.MIN, px)));
    document.documentElement.style.setProperty('--chat-h', h + 'px');
    setHSplitterAria(h);
  }

  function saveChatHeight() {
    try { localStorage.setItem(HSPLIT.KEY, String(chatHeightNow())); }
    catch (e) { /* 隐私模式 / 禁用存储：不记也行 */ }
  }

  function savedChatHeight() {
    try {
      var v = parseInt(localStorage.getItem(HSPLIT.KEY), 10);
      return isNaN(v) ? null : v;
    } catch (e) { return null; }
  }

  function resetChatHeight() {
    try { localStorage.removeItem(HSPLIT.KEY); } catch (e) { /* 同上 */ }
    document.documentElement.style.removeProperty('--chat-h');   // 交回 css 的 36vh 默认
    setHSplitterAria();
    toast('追问区高度已恢复默认');
  }

  /**
   * 横向分隔条：拖它改的是追问区高度。
   * 向上拖 = 追问区变高（把上面挤小，上面那块自己滚）。
   */
  function initSplitterH() {
    var sp = $('splitter-h');
    if (!sp) return;

    if (savedChatHeight() !== null && splitterUsable()) applyChatHeight(savedChatHeight());
    else setHSplitterAria();

    var dragging = false, startY = 0, startH = 0;

    sp.addEventListener('pointerdown', function (e) {
      if (!splitterUsable()) return;              // 与 CSS 断点对齐：隐藏时不响应
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      dragging = true;
      startY = e.clientY;
      startH = chatHeightNow();
      sp.classList.add('splitter--active');
      document.body.classList.add('is-resizing-h');
      if (sp.setPointerCapture) {
        try { sp.setPointerCapture(e.pointerId); } catch (err) { /* 忽略：退化为 document 监听 */ }
      }
      e.preventDefault();
    });

    document.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      // 用位移而不是绝对坐标：左栏内部会滚动，绝对位置会跳
      applyChatHeight(startH - (e.clientY - startY));
      e.preventDefault();
    });

    function endDrag() {
      if (!dragging) return;
      dragging = false;
      sp.classList.remove('splitter--active');
      document.body.classList.remove('is-resizing-h');
      saveChatHeight();
    }
    document.addEventListener('pointerup', endDrag);
    document.addEventListener('pointercancel', endDrag);

    sp.addEventListener('dblclick', function (e) { e.preventDefault(); resetChatHeight(); });

    sp.addEventListener('keydown', function (e) {
      if (!splitterUsable()) return;   // 单栏模式：分隔条已隐藏，键盘也不该改尺寸
      var k = e.key;
      var step = e.shiftKey ? HSPLIT.STEP_BIG : HSPLIT.STEP;
      // ↑ = 分隔条上移 = 追问区变高
      if (k === 'ArrowUp')              applyChatHeight(chatHeightNow() + step);
      else if (k === 'ArrowDown')       applyChatHeight(chatHeightNow() - step);
      else if (k === 'Home')            applyChatHeight(chatMaxHeight());  // 分隔条移到最上
      else if (k === 'End')             applyChatHeight(HSPLIT.MIN);       // 分隔条移到最下
      else if (k === 'Enter' || k === ' ') { resetChatHeight(); e.preventDefault(); return; }
      else return;
      saveChatHeight();
      e.preventDefault();
    });

    window.addEventListener('resize', function () {
      if (!splitterUsable()) {
        document.documentElement.style.removeProperty('--chat-h');
        return;                      // 单栏布局下 --chat-h 用不到，别留残留值
      }
      var saved = savedChatHeight();
      if (saved !== null) {
        applyChatHeight(saved);
      } else {
        document.documentElement.style.removeProperty('--chat-h');
        setHSplitterAria();
      }
    });
  }

  function boot() {
    initTabs();
    initEvents();
    initSplitter();
    initSplitterH();
    autoGrowChatInput();
    initConfig();
    checkHealth();
    setInterval(checkHealth, 15000);

    // 没有任务时，样例区保持空态
    refreshSamples();
    renderProgress();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window);
