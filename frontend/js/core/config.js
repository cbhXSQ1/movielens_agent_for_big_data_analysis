/* 同源读 config/*.json。必须与页面同源（静态服务要起在仓库根目录）。 */
export function createConfig({ base = '../config/', fetchImpl } = {}) {
  const doFetch = fetchImpl || ((...a) => fetch(...a));
  const cache = new Map();

  async function load(name) {
    if (cache.has(name)) return cache.get(name);
    const p = (async () => {
      try {
        const res = await doFetch(base + name);
        if (!res.ok) return { ok: false, error: { code: 'CONFIG_INVALID', message: `读不到 ${name}（HTTP ${res.status}）` } };
        return { ok: true, data: await res.json() };
      } catch (err) {
        return { ok: false, error: { code: 'CONFIG_INVALID',
          message: `读不到 ${name}：${err && err.message ? err.message : err}。静态服务需要起在仓库根目录。` } };
      }
    })();
    cache.set(name, p);
    const res = await p;
    if (!res || res.ok !== true) cache.delete(name);   // 失败不进缓存，否则重试点不动
    return res;
  }

  return {
    loadScoring: () => load('scoring_scheme.v1.json'),
    loadRules: () => load('cleaning_rules.v1.json'),
  };
}
