/* =============================================================================
   api.js —— 与 Agent HTTP 接口（agent/http_api.py）之间的唯一通道
   -----------------------------------------------------------------------------
   本文件只做三件事：
     1. 发请求（带超时、UTF-8、JSON 解析）
     2. 把「传输层失败」也归一化成和 driver 同构的错误信封
     3. 把错误码翻译成中文
   它**不加工任何业务数据**：拿到什么返回什么，绝不补数字。

   接口契约要点（已逐个核对 agent/http_api.py 源码）：
     · 出错时 HTTP 状态码可能仍是 200，**必须判断 body.ok**，不能只看状态码
     · stdout/响应体是一个 JSON 信封：{ok:true,...} 或 {ok:false,error:{code,message}}
     · /report?format=md 返回的是 **纯文本 Markdown**，不是 JSON
     · 中文请求体必须是 UTF-8（浏览器 fetch + JSON.stringify 默认即满足）
   ========================================================================== */

(function (global) {
  'use strict';

  /* ---------------------------------------------------------------- 配置 */

  /** 接口基址。换机器只改这一行。 */
  var API_BASE = 'http://localhost:8765';

  /** 单次请求超时（毫秒）。任务本身是异步的，所以这里只管接口响应。 */
  var TIMEOUT_MS = 20000;

  /** driver 的退出码 → 语义（docs/hadoop/agent-interface.md §2） */
  var EXIT_CODES = {
    0: '成功', 2: '参数或配置非法', 3: '任务失败',
    4: '任务不存在', 5: '任务尚未完成', 6: '版本或发布冲突'
  };

  /**
   * 错误码 → 中文说明与处置建议。
   * 前 7 个来自接口契约 §6；后 5 个是 driver_client 的传输层错误码。
   */
  var ERROR_INFO = {
    CONFIG_INVALID:       { text: '配置校验未通过',       advice: '展开 details 查看具体哪一条不合法。' },
    TASK_ALREADY_RUNNING: { text: '已有任务正在运行',     advice: '等它跑完，或在发起时显式允许并发。' },
    TASK_NOT_FOUND:       { text: '任务不存在',           advice: '检查任务标识，或重新发起一次任务。' },
    TASK_NOT_FINISHED:    { text: '任务尚未完成',         advice: '继续轮询进度；完成前不会显示任何结果数字。' },
    TASK_FAILED:          { text: '任务执行失败',         advice: '看失败环节与原因；修复后可重新发起。' },
    VERSION_CONFLICT:     { text: '版本冲突',             advice: '配置或输入变了但版本号没升，必须换一个数据版本。' },
    USAGE:                { text: '参数用法错误',         advice: '检查请求参数。' },
    DRIVER_UNREACHABLE:   { text: '调不到后端 driver',    advice: '确认仓库路径与 Python 环境可用。' },
    DRIVER_TIMEOUT:       { text: '后端响应超时',         advice: '任务可能仍在跑，稍后重试。' },
    DRIVER_NO_OUTPUT:     { text: '后端没有输出',         advice: '看后端 stderr 日志。' },
    DRIVER_BAD_OUTPUT:    { text: '后端输出不是合法 JSON', advice: '通常是编码或日志混入 stdout。' },
    NETWORK_ERROR:        { text: '连不上 Agent 服务',    advice: '先启动：python -m agent.http_api --port 8765' },
    BAD_RESPONSE:         { text: '响应无法解析',         advice: '检查服务是否被其它进程占用。' }
  };

  /* ---------------------------------------------------------------- 工具 */

  function errorEnvelope(code, message, extra) {
    var env = { ok: false, error: { code: code, message: message || '' } };
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) env.error[k] = extra[k];
      }
    }
    return env;
  }

  /** 把任意错误码翻译成人能读的一段话。 */
  function describeError(err) {
    if (!err) return { code: '', text: '未知错误', advice: '' };
    var info = ERROR_INFO[err.code] || { text: err.code || '未知错误', advice: '' };
    return { code: err.code || '', text: info.text, advice: info.advice, message: err.message || '' };
  }

  /** 拼接 query，跳过 null / undefined / 空串。 */
  function qs(params) {
    if (!params) return '';
    var parts = [];
    for (var k in params) {
      if (!Object.prototype.hasOwnProperty.call(params, k)) continue;
      var v = params[k];
      if (v === null || v === undefined || v === '') continue;
      parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    }
    return parts.length ? ('?' + parts.join('&')) : '';
  }

  /** 带超时的 fetch；网络层错误归一化成信封。 */
  function request(method, path, body, opts) {
    opts = opts || {};
    var url = API_BASE + path;
    var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = null;
    var init = { method: method, headers: {} };

    if (body !== undefined && body !== null) {
      init.headers['Content-Type'] = 'application/json; charset=utf-8';
      init.body = JSON.stringify(body);           // JSON.stringify 产出 UTF-8
    }
    if (ctrl) {
      init.signal = ctrl.signal;
      timer = setTimeout(function () { ctrl.abort(); }, opts.timeout || TIMEOUT_MS);
    }

    return fetch(url, init).then(function (res) {
      if (timer) clearTimeout(timer);
      if (opts.rawText) {
        // /report?format=md 走这条路：纯文本，不是 JSON
        return res.text().then(function (text) {
          return { ok: res.ok, status: res.status, text: text };
        });
      }
      return res.text().then(function (text) {
        var data = null;
        try { data = JSON.parse(text); } catch (e) { data = null; }
        if (data === null || typeof data !== 'object') {
          return errorEnvelope('BAD_RESPONSE',
            'HTTP ' + res.status + '，响应不是 JSON 对象',
            { raw: String(text).slice(0, 300) });
        }
        // 契约：出错时状态码可能仍是 200，判 ok 才是判据
        return data;
      });
    }).catch(function (err) {
      if (timer) clearTimeout(timer);
      var aborted = err && (err.name === 'AbortError');
      return errorEnvelope(aborted ? 'DRIVER_TIMEOUT' : 'NETWORK_ERROR',
        aborted ? ('请求超过 ' + ((opts.timeout || TIMEOUT_MS) / 1000) + ' 秒未返回')
                : ('无法连接 ' + API_BASE + '：' + (err && err.message ? err.message : err)));
    });
  }

  var get  = function (p, o) { return request('GET', p, null, o); };
  var post = function (p, b, o) { return request('POST', p, b || {}, o); };

  /* ---------------------------------------------------------------- 端点 */

  var api = {
    API_BASE: API_BASE,
    TIMEOUT_MS: TIMEOUT_MS,
    EXIT_CODES: EXIT_CODES,
    ERROR_INFO: ERROR_INFO,
    describeError: describeError,
    errorEnvelope: errorEnvelope,
    qs: qs,

    /** 健康检查：用于顶栏状态灯。 */
    health: function () { return get('/health', { timeout: 5000 }); },

    /** 已登记方案列表。注意 path 在 Windows 上是反斜杠，取用前要归一化。 */
    schemes: function () { return get('/api/schemes'); },

    /** 最近任务列表。 */
    tasks: function () { return get('/api/tasks'); },

    /**
     * 一次提示：把用户的一句人话交给 Agent，由它决定调哪个工具。
     * 返回 {ok, intent, intent_cn, reply, data, task_id}
     * exec_mode: 'cluster' | 'local'（省略则由 Agent 侧决定）
     */
    chat: function (text, taskId, execMode) {
      var body = { text: text };
      if (taskId) body.task_id = taskId;
      if (execMode) body.exec_mode = execMode;
      return post('/api/chat', body);
    },

    /** 结构化发起任务（供需要在 UI 上明确执行方式时使用）。 */
    startTask: function (opts) {
      opts = opts || {};
      var body = {};
      if (opts.rules) body.rules = opts.rules;
      if (opts.scoring) body.scoring = opts.scoring;
      if (opts.dataVersion) body.data_version = opts.dataVersion;
      if (opts.tag) body.tag = opts.tag;
      if (opts.execMode) body.exec_mode = opts.execMode;
      return post('/api/tasks', body);
    },

    /** 任务状态与进度。 */
    status: function (taskId) {
      return get('/api/tasks/' + encodeURIComponent(taskId) + '/status', { timeout: 30000 });
    },

    /** 权威结果。explain=true 时额外带回 _explanation（Agent 的中文说明）。 */
    result: function (taskId, explain) {
      return get('/api/tasks/' + encodeURIComponent(taskId) + '/result' + qs({ explain: explain ? 1 : '' }),
        { timeout: 60000 });
    },

    /** 样例：type=cleaned|quarantine，table=ratings|users|movies。 */
    samples: function (taskId, type, table, n) {
      return get('/api/tasks/' + encodeURIComponent(taskId) + '/samples' + qs({ type: type, table: table, n: n }),
        { timeout: 60000 });
    },

    /** 报告 JSON 信封。 */
    reportJson: function (taskId) {
      return get('/api/tasks/' + encodeURIComponent(taskId) + '/report' + qs({ format: 'json' }), { timeout: 60000 });
    },

    /**
     * 报告 Markdown 全文（纯文本）。
     * 成功时返回 {ok:true, text:"..."}；失败时返回错误信封。
     */
    reportMarkdown: function (taskId) {
      return request('GET', '/api/tasks/' + encodeURIComponent(taskId) + '/report' + qs({ format: 'md' }),
        null, { rawText: true, timeout: 60000 }).then(function (r) {
          if (r && r.text !== undefined && r.ok) {
            return { ok: true, task_id: taskId, report: r.text };
          }
          return errorEnvelope('TASK_NOT_FOUND', '报告未取到（HTTP ' + (r && r.status) + '）');
        });
    },

    /** 校验一份配置（用户自定义时使用）。 */
    validate: function (rulesPath, scoringPath) {
      var body = {};
      if (rulesPath) body.rules = rulesPath;
      if (scoringPath) body.scoring = scoringPath;
      return post('/api/validate', body);
    },

    /**
     * 读取仓库内的配置文件（评分依据页用）。
     * 前端与 config/ 必须同源：从仓库根目录起 http.server，访问 /frontend/。
     * 不硬编码任何规则内容，只负责把文件取回来。
     */
    loadConfig: function (relativePath) {
      var clean = String(relativePath || '')
        .replace(/\\/g, '/')        // Windows 反斜杠 → 正斜杠
        .replace(/^\.?\//, '');     // 去掉前导 ./ 或 /
      // index.html 位于 /frontend/，配置位于 /config/，因此往上一层
      var url = '../' + clean;
      return fetch(url, { method: 'GET' }).then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status + ' 读取 ' + url);
        return res.json();
      });
    }
  };

  global.ODAPI = api;
})(window);
