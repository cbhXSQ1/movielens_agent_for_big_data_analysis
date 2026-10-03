/* 唯一 HTTP 通道。页面不直接 fetch，只经这里。
   两个约定：report 走纯文本分支；scope 一律传 null 交给后端快照。 */
export const API_BASE_DEFAULT = 'http://localhost:8765';

const ERRORS = {
  TASK_NOT_FINISHED: '任务还没跑完',
  TASK_FAILED: '任务失败',
  TASK_NOT_FOUND: '找不到这个任务',
  TASK_ALREADY_RUNNING: '已有任务在跑',
  CONFIG_INVALID: '配置不合法',
  USAGE: '参数不对',
  DRIVER_UNREACHABLE: '连不上后端服务',
  INTERNAL_ERROR: '服务端出错了',
  VERSION_CONFLICT: '数据版本冲突',
  DEPENDENCY_MISSING: '缺少前置结果',
  LLM_UNSUPPORTED: '后端没开启大模型增强',
  LLM_DISABLED: '后端总开关没开',
  LLM_NOT_CONFIGURED: '大模型配置不完整',
  LLM_UNREACHABLE: '连不上大模型接口',
};

export function errorMessage(code, fallback) {
  return ERRORS[code] || fallback || '出错了';
}

const SAMPLES_N = [5, 20, 50];

export function createApi({ base = API_BASE_DEFAULT, fetchImpl, timeoutMs = 60000 } = {}) {
  const doFetch = fetchImpl || ((...a) => fetch(...a));

  async function req(path, { method = 'GET', body } = {}) {
    let res;
    try {
      res = await doFetch(base + path, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined,
      });
    } catch (err) {
      return { ok: false, error: { code: 'DRIVER_UNREACHABLE', message: `连不上 ${base}：${err && err.message ? err.message : err}` } };
    }
    let payload = null;
    try { payload = await res.json(); } catch { payload = null; }
    if (!res.ok || !payload || payload.ok === false) {
      const e = (payload && payload.error) || {};
      return { ok: false, error: { code: e.code || 'INTERNAL_ERROR', message: e.message || `HTTP ${res.status}` } };
    }
    return { ok: true, data: payload };
  }

  return {
    base,
    health: () => req('/health'),

    chat: ({ text, taskId, execMode, llm, autoStart }) => {
      const body = { text, scope: null };                 // scope 必须传 null
      if (taskId) body.task_id = taskId;
      if (execMode) body.exec_mode = execMode;
      if (typeof autoStart === 'boolean') body.auto_start = autoStart;
      if (llm) body.llm = llm;
      return req('/api/chat', { method: 'POST', body });
    },

    status: (id) => req(`/api/tasks/${encodeURIComponent(id)}/status`),
    result: (id, { explain = false } = {}) => req(`/api/tasks/${encodeURIComponent(id)}/result${explain ? '?explain=1' : ''}`),
    tasks: () => req('/api/tasks'),
    schemes: () => req('/api/schemes'),
    llmTest: (llm) => req('/api/llm/test', { method: 'POST', body: { llm } }),

    samples: ({ taskId, type = 'quarantine', table = 'ratings', n = 20 }) => {
      const safeN = SAMPLES_N.includes(n) ? n : 20;      // 非法 n 会让后端 500，这里先兜住
      const q = new URLSearchParams({ type, table, n: String(safeN) });
      return req(`/api/tasks/${encodeURIComponent(taskId)}/samples?${q}`);
    },

    async reportText(id) {
      let res;
      try {
        res = await doFetch(`${base}/api/tasks/${encodeURIComponent(id)}/report?format=md`,
                            { signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined });
      } catch (err) {
        return { ok: false, error: { code: 'DRIVER_UNREACHABLE', message: String(err && err.message || err) } };
      }
      let text;
      try {
        text = await res.text();
      } catch (err) {
        return { ok: false, error: { code: 'DRIVER_UNREACHABLE', message: String(err && err.message || err) } };
      }

      /* 后端只有成功且 format=md 时才发 text/plain；
         失败一律是 HTTP 200 + JSON 错误信封（agent/http_api.py:221-226）。 */
      const ct = (res.headers && typeof res.headers.get === 'function'
                  ? res.headers.get('content-type') : '') || '';
      if (res.ok && ct.includes('text/plain')) return { ok: true, text };

      try {
        const p = JSON.parse(text);
        const e = (p && p.error) || {};
        return { ok: false, error: { code: e.code || 'INTERNAL_ERROR', message: e.message || `HTTP ${res.status}` } };
      } catch {
        return { ok: false, error: { code: 'INTERNAL_ERROR', message: `HTTP ${res.status}` } };
      }
    },
  };
}
