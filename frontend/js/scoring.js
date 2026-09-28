/* =============================================================================
   scoring.js —— 「评分依据」页的渲染
   -----------------------------------------------------------------------------
   作业要求（迭代一 §6）原文：
     「评分依据：说明学生设计的评价方法、适用范围和局限；
       未完成、无法评价和未验证的情况应清楚标注。」

   本文件的数据**全部来自仓库里的声明式配置**，前端不硬编码任何规则、阈值、
   指标或权重。三块内容：
     1. 评分方案 scoring_scheme.v1.json → 五维定义 + 每个指标的说明/局限/无法验证项
     2. 清洗方案 cleaning_rules.v1.json → 按流水线阶段分组的规则清单
     3. 任务结果里的口径差异说明（有则显示，没有就如实说没有）

   三类「要标注的情况」怎么落地（对应作业原文的三个词）：
     · 未完成   → 任务未成功时整页显示"本次未产出"，不显示任何 0 分
     · 无法评价 → 指标自带的 limitations
     · 未验证   → 指标自带的 unverifiable[] + 任务级 limitations
   ========================================================================== */

(function (global) {
  'use strict';

  var charts = global.ODCharts;

  /* ------------------------------------------------- UI 词汇（不是领域规则） */

  var ACTION_ZH = {
    fix: '修复', quarantine: '隔离', dedupe: '去重', dedupe_resolve: '去重并解决冲突',
    mark: '仅标记', check: '仅检查', report: '仅报告'
  };

  var STAGE_ZH = {
    parse: '解析阶段', validate_repair: '校验与修复', dedupe_resolve: '去重与冲突解决',
    residual_checks: '兜底检查', cross_table: '跨表校验', report: '报告类规则'
  };

  function actionZh(a) { return ACTION_ZH[a] || a || '—'; }
  function stageZh(s) { return STAGE_ZH[s] || s || '—'; }

  /* ---------------------------------------------------------------- 缓存 */

  var cache = { scoring: null, cleaning: null, error: null };

  /**
   * 读取两份配置（只读、不改）。失败时把原因原样返回，由调用方如实展示。
   * 依赖：页面与 config/ 同源 —— 请从仓库根目录起 http.server 后访问 /frontend/。
   */
  function load() {
    if (cache.scoring && cache.cleaning) return Promise.resolve(cache);
    return Promise.all([
      global.ODAPI.loadConfig('config/scoring_scheme.v1.json'),
      global.ODAPI.loadConfig('config/cleaning_rules.v1.json')
    ]).then(function (pair) {
      cache.scoring = pair[0];
      cache.cleaning = pair[1];
      return cache;
    }).catch(function (err) {
      cache.error = err;
      throw err;
    });
  }

  /* ------------------------------------------------------------ 错误/空态 */

  function errorState(err) {
    var box = document.createElement('div');
    box.className = 'state-error';
    var t = document.createElement('p'); t.className = 'state-error__title';
    t.textContent = '配置文件没读到';
    var c = document.createElement('p'); c.className = 'state-error__cause';
    c.textContent = String(err && err.message ? err.message : err);
    var h = document.createElement('p'); h.className = 'state-error__hint';
    h.textContent = '本页需要与 config/ 同源。请从仓库根目录启动服务：python -m http.server 8080，再访问 http://localhost:8080/frontend/';
    box.appendChild(t); box.appendChild(c); box.appendChild(h);
    return box;
  }

  /* -------------------------------------------------- 1) 五维 + 指标渲染 */

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  /** 渲染五个维度与全部指标。 */
  function renderDimensions(scoring, host) {
    host.textContent = '';
    if (!scoring || !scoring.dimensions) {
      host.appendChild(el('p', 'empty__body', '评分方案里没有 dimensions 数组。'));
      return;
    }

    // 方案元信息
    var meta = el('div', 'kv');
    [['方案标识', scoring.scheme_id], ['版本', scoring.version], ['状态', scoring.status],
     ['创建', scoring.created]].forEach(function (row) {
      if (row[1] === undefined || row[1] === null) return;
      meta.appendChild(el('dt', null, row[0]));
      meta.appendChild(el('dd', 'mono', row[1]));
    });
    host.appendChild(meta);

    if (scoring.description) host.appendChild(el('p', 'dim__def', scoring.description));

    scoring.dimensions.forEach(function (dim) {
      var box = el('div', 'dim');

      var head = el('div', 'dim__head');
      var name = el('div', 'dim__name');
      name.appendChild(el('span', null, dim.name_zh || dim.id));
      name.appendChild(document.createTextNode(' '));
      var codeEl = el('code', null, dim.id);
      name.appendChild(codeEl);
      head.appendChild(name);
      if (dim.weight !== undefined) head.appendChild(el('span', 'dim__weight', '权重 ' + fmtWeight(dim.weight)));
      box.appendChild(head);

      if (dim.definition) box.appendChild(el('p', 'dim__def', dim.definition));

      (dim.metrics || []).forEach(function (m) {
        box.appendChild(renderMetric(m));
      });

      host.appendChild(box);
    });

    // 综合分
    if (scoring.composite) {
      var cBox = el('div', 'dim');
      cBox.appendChild(el('div', 'dim__name', '综合分'));
      var c = scoring.composite;
      var line = c.enabled === false
        ? '本方案未启用综合分。'
        : '本方案启用综合分，按维度权重加权求和。';
      cBox.appendChild(el('p', 'dim__def', line));
      if (c.weights) {
        var w = el('div', 'kv');
        Object.keys(c.weights).forEach(function (k) {
          w.appendChild(el('dt', null, k));
          w.appendChild(el('dd', 'mono', fmtWeight(c.weights[k])));
        });
        cBox.appendChild(w);
      }
      host.appendChild(cBox);
    }
  }

  function fmtWeight(w) {
    if (w === null || w === undefined || isNaN(w)) return '—';
    return Number(w).toFixed(2);
  }

  /** 单个指标卡：说明 / 适用范围与局限 / 无法验证项。 */
  function renderMetric(m) {
    var box = el('div', 'metric');

    var head = el('div', 'metric__head');
    head.appendChild(el('span', 'metric__id', m.id));
    head.appendChild(el('span', 'metric__name', m.name || ''));
    if (m.weight !== undefined) head.appendChild(el('span', 'dim__weight', '指标权重 ' + fmtWeight(m.weight)));
    if (m.measure) head.appendChild(el('span', 'dim__weight', m.measure));
    if (m.enabled === false) head.appendChild(el('span', 'tag tag--muted', '本次未启用'));
    box.appendChild(head);

    if (m.description) box.appendChild(el('p', 'metric__desc', m.description));

    if (m.limitations) {
      var lim = el('p', 'metric__flag metric__flag--limit');
      lim.appendChild(el('b', null, '适用范围与局限：'));
      lim.appendChild(document.createTextNode(m.limitations));
      box.appendChild(lim);
    }

    var unver = Array.isArray(m.unverifiable) ? m.unverifiable : null;
    var flag = el('p', 'metric__flag metric__flag--unver');
    flag.appendChild(el('b', null, '无法验证项：'));
    flag.appendChild(document.createTextNode(
      (unver && unver.length) ? unver.join('；') : '无（本指标不存在无法验证的项）'));
    box.appendChild(flag);

    return box;
  }

  /* ------------------------------------------------------ 2) 清洗规则清单 */

  /** 按 pipeline 的阶段顺序分组渲染规则。 */
  function renderRules(cleaning, host) {
    host.textContent = '';
    if (!cleaning || !Array.isArray(cleaning.rules)) {
      host.appendChild(el('p', 'empty__body', '清洗方案里没有 rules 数组。'));
      return;
    }

    var byId = {};
    cleaning.rules.forEach(function (r) { byId[r.id] = r; });

    var meta = el('div', 'kv');
    [['方案标识', cleaning.scheme_id], ['版本', cleaning.version], ['状态', cleaning.status],
     ['规则总数', cleaning.rules.length]].forEach(function (row) {
      if (row[1] === undefined || row[1] === null) return;
      meta.appendChild(el('dt', null, row[0]));
      meta.appendChild(el('dd', 'mono', row[1]));
    });
    host.appendChild(meta);

    // 数据版本与时间边界（配置里登记的 T1/T2）
    var dv = cleaning.data_version;
    if (dv) {
      var dvBox = el('div', 'kv');
      dvBox.appendChild(el('dt', null, '数据版本'));
      dvBox.appendChild(el('dd', 'mono', dv.id || '—'));
      var tb = dv.time_boundaries || {};
      if (tb.T1) { dvBox.appendChild(el('dt', null, 'T1 训练期截止')); dvBox.appendChild(el('dd', 'mono', tb.T1)); }
      if (tb.T2) { dvBox.appendChild(el('dt', null, 'T2 验证期截止')); dvBox.appendChild(el('dd', 'mono', tb.T2)); }
      host.appendChild(dvBox);
    }

    var pipeline = Array.isArray(cleaning.pipeline) ? cleaning.pipeline : null;
    if (!pipeline) {
      // 没有 pipeline 就按 rules 原顺序平铺，不臆造阶段
      host.appendChild(renderRuleTable(cleaning.rules));
      return;
    }

    pipeline.forEach(function (st) {
      var wrap = el('div', 'dim');
      var head = el('div', 'dim__head');
      head.appendChild(el('div', 'dim__name', stageZh(st.stage)));
      head.appendChild(el('span', 'dim__weight', (st.rules || []).length + ' 条'));
      wrap.appendChild(head);

      var list = (st.rules || []).map(function (id) { return byId[id]; }).filter(Boolean);
      wrap.appendChild(renderRuleTable(list));
      host.appendChild(wrap);
    });
  }

  function renderRuleTable(rules) {
    var wrap = el('div', 'table-wrap');
    var table = el('table', 'data');
    var thead = el('thead');
    var hr = el('tr');
    ['规则', '名称', '处置', '判据说明'].forEach(function (h) { hr.appendChild(el('th', null, h)); });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el('tbody');
    rules.forEach(function (r) {
      var tr = el('tr');
      tr.appendChild(el('td', 'mono', r.id));
      tr.appendChild(el('td', null, r.name || '—'));
      var act = el('td');
      var tagCls = r.action === 'quarantine' ? 'tag tag--warning'
                 : (r.action === 'fix' ? 'tag tag--success' : 'tag tag--muted');
      act.appendChild(el('span', tagCls, actionZh(r.action)));
      tr.appendChild(act);
      tr.appendChild(el('td', null, r.description || '—'));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  /* --------------------------------------------- 3) 口径差异说明（可选字段） */

  /**
   * 渲染任务结果里的 rule_notes。
   * 该字段未出现在接口文档 §4.5 的示例里，是实测发现的 —— 所以：
   * 有就显示，没有就明说"本次无"，绝不假设它在。
   */
  function renderRuleNotes(notes, host) {
    host.textContent = '';

    if (!notes) {
      var e1 = el('div', 'empty');
      e1.appendChild(el('p', 'empty__title', '本次没有口径差异说明'));
      e1.appendChild(el('p', 'empty__body',
        '后端未在结果里返回该字段。这不代表没有问题，只代表本次没有额外的口径说明可展示。'));
      host.appendChild(e1);
      return;
    }

    var box = el('div', 'rulenote');
    if (notes.headline) box.appendChild(el('p', 'rulenote__headline', notes.headline));

    var list = Array.isArray(notes.notes) ? notes.notes : [];
    if (!list.length) {
      box.appendChild(el('p', 'empty__body', '说明里没有具体条目。'));
    }

    list.forEach(function (n) {
      var item = el('div', 'rulenote__item');
      var title = el('div', 'rulenote__title');
      title.appendChild(el('code', null, n.rule_id || '—'));
      title.appendChild(document.createTextNode(' ' + (n.name || '')));
      if (n.action) title.appendChild(el('span', 'tag tag--muted', actionZh(n.action)));
      item.appendChild(title);

      var pairs = [
        ['代码实际判据', n.detect_semantics],
        ['判据实测结果', n.detect_observed],
        ['配置备注写的', n.evidence_says],
        ['为什么不一样', n.why_different],
        ['对契约的影响', n.contract_impact]
      ];
      var dl = el('dl', 'rulenote__pair');
      pairs.forEach(function (p) {
        if (!p[1]) return;
        dl.appendChild(el('dt', null, p[0]));
        dl.appendChild(el('dd', null, p[1]));
      });
      item.appendChild(dl);
      box.appendChild(item);
    });

    host.appendChild(box);
  }

  global.ODScoring = {
    load: load,
    renderDimensions: renderDimensions,
    renderRules: renderRules,
    renderRuleNotes: renderRuleNotes,
    errorState: errorState,
    actionZh: actionZh,
    stageZh: stageZh
  };
})(window);
