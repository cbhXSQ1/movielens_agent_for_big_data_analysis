/* 顶栏的两个动态元素：后端状态点与「数据版本」chip。都是纯订阅，没有别的状态。
   裁定 R16：spec 第 163 行「顶栏 品牌 · 数据版本 · 后端状态 · [设置]」与 brief Step 5
   「/health 成功后，右上角绿点 + 「后端已连接」」都要求顶栏这两个元素跟着 store 走，
   但 index.html 的 #health、#chip-data-version 与 shell.css 的 `.health[data-ok]`
   全树没有生产者。此处一处订阅各驱动一个元素，不引入别的状态。 */
export function createTopbar({ store }) {
  function start() {
    const health = document.getElementById('health');
    const ver = document.getElementById('chip-data-version');

    store.subscribe(state => {
      const ok = state.health.ok;                  // 三态：null 还没探过 → 灰点
      health.dataset.ok = String(ok);
      health.querySelector('.health__text').textContent =
        ok === null ? '检测后端…' : (ok ? '后端已连接' : '后端未连接');
    });

    /* 裁定 R63：spec §4.2 把「数据版本」定为顶栏唯一的 meta chip，index.html 里也写死了它，
       但全树没有生产者 —— 每个视图上都停在「数据版本 —」。结果到手就写实，没有结果就退回「—」，
       与 spec §7.4 一致：没跑完的任务不猜版本号。 */
    store.subscribe(state => {
      const v = state.result && state.result.dataVersion;
      ver.textContent = `数据版本 ${v || '—'}`;
    });
  }

  return { start };
}
