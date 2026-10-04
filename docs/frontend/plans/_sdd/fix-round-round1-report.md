# 修复轮 round1 报告 —— F1 死锁 / F2 残留报告 / F3 文案 / F4 R3 常驻 / F5 占位

- 分支 `main`，BASE `dba5e9f`；一个提交，显式 `git add`（见 §7）。
- 范围：只动 `frontend/` 与 `docs/`。`agent/`、`tokens.css` 零改动（`git diff --stat` 可核）。
- 四道门全绿（本轮新加的白屏守卫也跑在提交后的 HEAD 上）：见 §6。

**先做的事：按 `docs/frontend/plans/_sdd/task-14-report.md` §315 的建议把任务会话抽成
`frontend/js/shell/tasks.js`。** 这是纯搬迁，行为一行没变，抽完 `main.js` 从 **248 → 138 行**，
F1 才有地方落地。搬迁 + 五条修复都在同一个提交里（§1–§5 逐条对照）。

---

## 0. 行数账

口径说明：下面用 **`read` 工具 / `git show | wc -l`** 的口径（文件没有行尾空行时的真实行数）。
`check.mjs` 内部用的是 `split(/\r?\n/).length`，那套算法对末尾带换行的文件会**多算 1**：
对 `frontend/tools/check.mjs`（本轮未改、`read` 口径 247）它给 248 —— 与派单里写的 248/250 一致。
两个口径都远在 250 之内，`check.mjs §10.13` 判 PASS（它就是 ≤250 那道门）。

| 文件 | 前 | 后 | 余量 |
|---|---|---|---|
| `frontend/js/main.js` | 248 | **137** | +113 |
| `frontend/js/shell/tasks.js`（新） | — | 151 | +99 |
| `frontend/js/shell/taskbar.js` | 241 | 248 | **+2** |
| `frontend/js/views/evidence.js` | 207 | 228 | +22 |
| `frontend/js/views/cleaning.js` | 230 | 230 | +20 |
| `frontend/js/views/overview.js` | 194 | 200 | +50 |
| `frontend/js/core/run-state.js` | 122 | 129 | +121 |
| `frontend/css/views/cleaning.css` | 38 | 44 | +206 |
| `frontend/tools/check.mjs` | 247 | 247（**未动**） | +3 |

`main.js`：**248 → 137 行**（派单要求"降到 170 行上下"，实际更低）。
新文件 `tasks.js` 151 行 —— 搬迁出来的那一族（`startPolling`/`tick`/`stopPolling`/`showTask`/
`resumeLastTask`/`startNewTask`/`loadResult`/`taskFrom`/`loadTasks`/`isBusy`）在这里有了完整注释。

---

## 1. F1（Critical）：「新任务」把 UI 锁死 —— 已修，并真点过

### 1.1 修法（两条一起做）

**① 运行中「新任务」也 `disabled`，与发送键同一判据**

`frontend/js/shell/taskbar.js`：

```js
/* F1：运行中「新任务」也要灰掉，理由与发送键逐字相同（B1 的决定：运行中不许起新任务）。 */
const BUSY_TITLE = '任务正在跑，跑完才能清空当前结果';

export function createTaskbar({ host, store, onPickTask, onNewTask, loadTasks, isBusy }) {
```

`render()` 开头（**不是**在有任务的那个分支里，首屏也要判）：

```js
    const busy = !!(isBusy && isBusy());
    newBtn.disabled = busy;
    newBtn.title = busy ? BUSY_TITLE : '清空当前结果，然后在下面对 Agent 说话';
```

判据由 `frontend/js/shell/tasks.js` 提供（`isBusy()` = `task.status` 是 `queued`/`running`），
`main.js` 装配时 `isBusy: tasks.isBusy` 与 composer 的发送键同源：

```js
const composer = createComposer({ … });
```

`composer.js` 一个字没改，它的灰态仍然是
`sending || !!s.liveTaskId || task.status === 'queued' || task.status === 'running'`。

**② `startNewTask` 里防御性清 `liveTaskId`（+ `stageTotal`）**

`frontend/js/shell/tasks.js`：

```js
  function startNewTask() {
    clearTimer();
    /* F1：清展示就一并把 `liveTaskId` 清掉。判据上这条路径本不该在运行中被走到
       （「新任务」按钮此时是 disabled 的），但**死锁不能再有第二条路**：
       发送键的灰态把 `liveTaskId` 也算作"忙"，留着它就是一个刷不掉的灰键。 */
    store.set({ liveTaskId: null,
      /* 形状与 store 初始的 task 一致（含 stageTotal）：缺了它，任务条印 `—`
         而视图印 `0/0`，同一屏两个说法。 */
      task: { id: null, status: null, stage: null, stageIndex: 0, stageTotal: 9, percent: 0,
              startedAt: null, finishedAt: null, opts: {}, errors: [] },
      result: null, resultError: null });
  }
```

死锁的两条路都断了：运行中点不动（①），万一被走到也不会留下灰键（②）。

### 1.2 真点实测（这一条是死锁，按硬约束走完整路径）

驱动：`docs/frontend/plans/_sdd/drivers/browser-check-fix-round1.mjs`（前半）与
`browser-check-fix-round1-part2.mjs`（后半）。**全部点击走 `Input.dispatchMouseEvent` 真实坐标**，
「切任务」走真实「历史任务」弹层里那一行，运行中/终态的 `/status` 由 CDP Fetch 域在浏览器层给。
页面代码一行没为验收改过。

前半段（在左栏**真打字、真按发送键**起一个 running 任务）：

```
  PASS  F1-1 首屏：发送键可用
  PASS  F1-1 首屏：「新任务」可用
  PASS  F1-2 运行中：「新任务」变灰
  PASS  F1-2 运行中：「新任务」title 说明原因
  PASS  F1-2 运行中：发送键也灰着（同一判据）
  PASS  F1-2 运行中：任务条写「进行中」
  PASS  F1-2 运行中：liveTaskId 非空
  PASS  F1-3 运行中点「新任务」无效（按钮不可点）
  PASS  F1-3 运行中点「新任务」无效（任务号还在）
  PASS  F1-3 运行中点「新任务」无效（liveTaskId 未被清）
  PASS  F1-4 终态：轮询停止，liveTaskId 被清
  PASS  F1-4 终态：「新任务」恢复可用
  PASS  F1-4 终态：发送键恢复可用
  PASS  F1-5 点完「新任务」：发送键**不灰**（死锁已解）
  PASS  F1-5 点完「新任务」：liveTaskId 为空
  PASS  F1-5 点完「新任务」：任务号已清
  PASS  F1-5 点完「新任务」：任务条回到「还没有任务」
  PASS  F1-5 点完「新任务」：页面回到首屏引导
  PASS  F1-6 startNewTask 的任务对象含 stageTotal（Minor 6）
  PASS  F1-6 没有「进行中」的任务条残留
失败项：0
```

后半段（`resumeLastTask` 那条真路径：`localStorage` 种一个 running 任务 → 刷新接回 →
**让真的 3 秒轮询自己发现终态** → 真点「新任务」）：

```
  PASS  F1-7 刷新接回运行中任务：任务条写「进行中」
  PASS  F1-7 liveTaskId 已被 startPolling(last, {}) 播种
  PASS  F1-7 运行中：「新任务」是灰的
  PASS  F1-7 运行中：「新任务」title 说明原因
  PASS  F1-7 运行中：发送键也是灰的
  PASS  F1-8 轮询自己发现终态：liveTaskId 清空
  PASS  F1-8 终态后「新任务」恢复可用
  PASS  F1-8 终态后发送键恢复可用
  PASS  F1-9 真点「新任务」：按钮确实可点（不是 disabled 挡掉的）
  PASS  F1-9 真点「新任务」后：发送键**不灰**（死锁解除）
  PASS  F1-9 真点「新任务」后：liveTaskId 为空
  PASS  F1-9 真点「新任务」后：任务号清空
  PASS  F1-9 真点「新任务」后：任务条回到「还没有任务」
  PASS  F1-9 真点「新任务」后：页面回到首屏引导
  PASS  F1-10 startNewTask 的任务对象含 stageTotal（Minor 6）
  PASS  F1-10 没有「进行中」残留
失败项：0
```

逐字证据（`docs/frontend/plans/_sdd/fix-round1-f1-running.json` / `fix-round1-part2-f1.json`）：

```json
"running": {
  "liveTaskId": "20261004-104643-b3ba4a",
  "taskStatus": "running",
  "taskbarText": "20261004-104643-b3ba4a进行中全量评分表清洗3/9本版不支持取消，跑完即可2:59新任务历史任务",
  "newTask": { "disabled": true, "title": "任务正在跑，跑完才能清空当前结果" },
  "send":    { "disabled": true, "title": "任务正在跑，本版不支持取消，跑完才能发下一句" }
},
"afterClickWhileRunning": { "liveTaskId": "20261004-104643-b3ba4a", "taskStatus": "running" },
"hitsAfterSend": { "/api/chat": 1, "/status": 1 }
```

```json
"afterNewTask": {
  "liveTaskId": null, "taskId": null, "taskStatus": null,
  "taskbarText": "还没有任务新任务历史任务",
  "newTaskDisabled": false, "sendDisabled": false,
  "guide": "在左栏对 Agent 说一句话就开始，比如「用默认规则清洗并评估五维质量」；也可以从「历史任务」里挑一次已有结果看。",
  "stageTotal": 9
}
```

截图：`fix-round1-f1-running.png`（运行中：「新任务」灰）、`fix-round1-f1-terminal.png`、
`fix-round1-f1-after-newtask.png`、`fix-round1-part2-f1-reload-running.png`、
`fix-round1-part2-f1-after-terminal.png`、`fix-round1-part2-f1-after-newtask.png`。

### 1.3 一个必须写下来的验收环境坑（否则证据是假的）

驱动第一版**看起来**全绿，其实页面第二段装载后 `window.__APP__` 是 `undefined`、任务条是空的 ——
点击全落在一个空页面上。三个小时的排查结论（都已写进驱动注释，供后续复用）：

1. **导航期间让 CDP `Fetch` 域开着**，下一次 `Page.navigate` 会卡住：`Page.loadEventFired`
   永不触发，页面停在静态 HTML（`document.documentElement.innerHTML` 3226 字、任务条 0 元素、
   `__APP__` undefined），而 `Fetch.requestPaused` 一次都不来。所以拦截只在"页面已经起来"时开；
   装载期必须拦的话用**窄 pattern**（只拦 `*/api/tasks/*/status`），不碰文档与模块本身。
2. 直接 `Page.navigate` 到**与当前完全相同**的 URL（含 hash）只是同文档导航：页面不重新装载、
   `localStorage` 不重读。要先绕一次 `about:blank`。
3. `resumeLastTask` 的 `/status` 在**页面装载期间**发出，"页面起来之后再开拦截"已经晚了 ——
   真后端的答案先到，横幅按真失败态渲染。规则必须在导航前装好。
4. 自己 fulfill 的跨源响应要自己补 `Access-Control-Allow-Origin`，否则浏览器按 CORS 失败处理
   （实测页面里是 `连不上 http://localhost:8765：Failed to fetch`）。

---

## 2. F2（Critical）：「证据」页留下上一个任务的报告 —— 已修，并造了在飞响应

### 2.1 修法（两道都要有）

`frontend/js/views/evidence.js`。第一道：丢弃分支**两块一起重画**。做法是把
"清空 + 占位"抽成 `paintCurrent(s)`（`update` 与两个取数函数的丢弃分支共用同一份实现，
不会两处各画各的）：

```js
  /* 按当前状态画两块面板的"非数据"内容：清空 + 占位（运行中 / 空态 / 首屏引导）。 */
  paintCurrent(s) {
    const running = runningNote(s.task, '这里可以浏览隔离区与清洗后的样例，并打印评估报告。');
    clear(this.tableHost);
    clear(this.reportHost);
    this.printBtn.disabled = true;
    …
  },
```

`loadSamples` 的丢弃分支（旧代码只 `clear(this.tableHost)`，`reportHost` 原地不动 ——
这就是"样例空着、报告还挂着上一个任务三页"的那条路，它**不经过** `update` 的 clear，绕过红线 R1）：

```js
      if (this.stale(key)) { this.paintCurrent(this.ctx.store.get()); return; }
```

第二道：`loadReport` 的**时效性守卫**（拿回响应后先比对任务号是否还是发起时那个）：

```js
      if (this.stale(key)) { this.paintCurrent(this.ctx.store.get()); return; }
```

`stale(key)` = `this.loadedTaskId !== key`，与 `loadSamples` 完全同一份判据（R61 在视图层的同一件事）。

### 2.2 在飞响应实测

同两个驱动。造法：CDP Fetch 域拦住 `/report`，**延迟 4 秒**再 fulfill（内容是真后端那份报告，
从 Node 侧现拉，2725 字），然后在它回来之前真点「新任务」。

对照（不延迟，真点「历史任务」里那一行）：

```
  PASS  F2-0 对照：点开真夹具后「证据」页画出真报告（>200 字）
  PASS  F2-0 对照：样例表也画出来了
```

实验：

```
  PASS  F2-1 /report 确实在飞（切换那一刻报告还没画出来）
  PASS  F2-1 /report 被拦到过一次（慢响应确实在飞）
  PASS  F2-2 慢响应回来后 reportHost 里**没有**上一个任务的报告
  PASS  F2-2 report 元素不存在（旧任务的三页报告没有落到新任务页面上）
  PASS  F2-3 「样例」也没有上一个任务的表
  PASS  F2-4 页面停在首屏引导（新任务已生效）
失败项：0
```

逐字证据（`fix-round1-f2.json`）：

```json
"hitsInflight": {},
"afterHits": { "/report": 1 },
"panelsAfter": [
  "在左栏对 Agent 说一句话就开始，比如「用默认规则清洗并评估五维质量」；也可以从「历史任务」里挑一次已有结果看。",
  "—",
  "—"
],
"after": { "reportLen": 0, "reportHostLen": 1, "taskId": null }
```

`afterHits` 里 `/report` **1 次**说明这条慢响应真的在飞、也真的回来了；回来后
`reportHost` 只有 1 个字（「—」），`report` 元素不存在。截图
`fix-round1-f2-control.png` / `fix-round1-f2-inflight.png` / `fix-round1-f2-after-inflight.png`。

**修前的同一场景**（只读代码不实测，按题面）：`loadSamples` 的丢弃分支只清 `tableHost`，
`reportHost` 里 `article.report` 原样留着；`loadReport` 没有任何守卫，回来的报告直接写进
`reportHost`。两道都缺，所以两条路都能把别人的数据留在页面上。

---

## 3. F3（Important）：B3 的文案在两个最常见场景里说反了 —— 改成中性，且只写了一定成立的那半句

### 3.1 修法

`frontend/js/core/run-state.js` 的 `scope-unknown` 分支：

```js
    out.push({
      key: 'scope-unknown', kind: 'muted',
      title: '运行设置未知',
      text: '这次运行没有留下运行设置（全量 / 抽样）的记录，所以这里不标注。评分标准与全量模式相同。',
    });
```

改前 / 改后：

| | 标题 | 正文 |
|---|---|---|
| 前 | 历史任务不记录运行设置 | 这是正常的：运行设置只随这一轮的执行过程回传。页面重新载入或切换到历史任务后，它就拿不到了。 |
| 后 | 运行设置未知 | 这次运行没有留下运行设置（全量 / 抽样）的记录，所以这里不标注。评分标准与全量模式相同。 |

### 3.2 为什么不写"结果本身不受影响"（题面要我按事实判断并说明理由）

先说结论：**这句话不许写**。理由是读了后端的真实契约，不是感觉：

- `agent/tools.py:55-71`：`scope` 只有 `full` / `sample` 两个值，`start_cleaning_task(scope=…)`
  把它交给 driver。**抽样改的是喂给整条流水线的数据集**（`sample` = 评分表前 2,000 行，
  `agent/tools.py:62` 的注释与 `agent/agent.py:278-283` 的用户可见文案都这么写），
  不是改评分算法。
- 所以"**评分标准没变**"成立（同一套 `clean_rate → score_before → score_after → aggregate`），
  但"**分数本身不受影响**"不成立 —— 分母小了、样本少了，分数当然会动。
  `agent/agent.py:279-280` 甚至明写抽样"这些数字不属于正式口径，不能用于汇报"。
- spec §7.4 是"宁可少说不可错说"：能写的是"评分标准相同"，不能写的是"结果不受影响"。
  所以我只写了前者，并且**没有**加任何安慰性的"不影响结论"。

### 3.3 实测（最常画面：页面刚打开就接回一个**正在跑**的任务）

```
  PASS  F3-1 未知口径横幅存在
  PASS  F3-2 标题里没有「历史」
  PASS  F3-3 正文里没有「历史」
  PASS  F3-4 不再断言"不记录运行设置"
  PASS  F3-5 与任务条「进行中」不矛盾
  PASS  F3-6 标题是中性的「运行设置未知」
失败项：0
```

同屏逐字（`fix-round1-part2-f3.json`，任务条与横幅取自同一次 `dump()`）：

```
任务条：20261004-104643-b3ba4a进行中全量评分表清洗3/9本版不支持取消，跑完即可1:00新任务历史任务
横幅：  运行设置未知｜这次运行没有留下运行设置（全量 / 抽样）的记录，所以这里不标注。评分标准与全量模式相同。
```

自相矛盾消失：任务条说「进行中」，横幅不再说"历史任务"。截图
`fix-round1-part2-f3-running-banner.png`。

---

## 4. F4（Important）：R3 的「隔离不等于修复」恢复常驻 —— 先解决了 B 批的技术理由

### 4.1 B 批改动的理由（先读后决定）

在 `frontend/js/views/cleaning.js` 找到 B 批的原文（`git log -p` 定位到 `5ef32cc`）：

```
+/* 顶部那条 notice 的三种状态，一处写完：
+   - 运行中 → 一句"完成以后会出现什么"（T5，不含任何数据）
+   - 没有 note 内容（没有任务、没有结果）→ 整块隐藏：空盒子会留出一条带边框的空白横条
+   - 有结果 → 红线 R3 的「隔离不等于修复」常驻声明
+   隐藏/显示用元素的 `hidden`，不动 CSS —— `.notice` 的边框与内边距在 cleaning.css 里，
+   给空内容留位反而更怪。 */
+  note.hidden = true;
```

**这个技术理由成立，但它不是一个真矛盾**：B 批把"没有内容"和"必须常驻"当成二选一，
而 R3 那句阅读须知**永远有内容** —— 冲突只来自"整块 notice 由状态决定装不装"。
把常驻声明固定在 DOM 里、只让"运行中说明"那一行随状态显隐，空盒子就不可能出现了。
所以不是"简单回退"，是把两种内容拆开。

### 4.2 修法

`cleaning.js: mount()` 里一次建好、永不隐藏（`update` 只改那一行的文字与显隐）：

```js
    /* 红线 R3 的常驻提示条：`#/cleaning` 顶部**任何状态下都在**，所以它一次建好、永不隐藏。 */
    this.note = el('div', 'notice');
    const running = el('p', 'notice__running');
    running.hidden = true;
    this.runningNote = running;
    this.note.appendChild(running);
    const red = el('p');
    red.appendChild(redline('隔离不等于修复',
      '：被隔离的记录是移出正表，不是被改对了。分数提升有一部分来自分母变小。'));
    this.note.appendChild(red);
```

`fillNote` 从"三种状态里挑一种、可能整块隐藏"缩成一行的事：

```js
function fillNote(note, running) {
  const line = note.querySelector('.notice__running');
  if (!line) return;
  line.textContent = running || '';
  line.hidden = !running;
}
```

`cleaning.css` 只加了"段间距与主色"，没有颜色字面量（G5）：

```css
.notice p { margin: 0; }
.notice__running + p { margin-top: var(--sp-2); }
.notice__running { color: var(--fg); }
```

顺带：`update()` 里 `flow` 那句引导原来写作 `if (key === 'flow' && guide && !running)`，
现在 `fillNote` 已经把 running 说明收进顶部 notice，面板 body 里不再重复，判据简化成 `if (key === 'flow' && guide)`
（`guide` 只在"没有任务且没有结果"时非空，那时 `running` 必为空，行为等价）。

**顺手修掉一个本轮实测抓到的真缺陷**：`this.note.appendChild(el('p', null, redline(...)))` ——
`el()` 对第三参走 `textContent = String(text)`，整个 DocumentFragment 会渲染成
**`[object DocumentFragment]`**（浏览器实测逐字抓到）。已改成先建空 `<p>` 再 `appendChild`。
这条是 F4 落地过程中被抓到的，不是题面要求，但不修的话 R3 那句话根本读不出来。

### 4.3 三条路径实测（首屏 / 运行中 / 有结果）

```
  PASS  F4-1 首屏：提示条在 DOM 里
  PASS  F4-2 首屏：提示条可见（不是 hidden、不是 0 高）
  PASS  F4-3 首屏：写的是「隔离不等于修复」
  PASS  F4-4 首屏：运行说明那一行是隐藏的（不冒充状态）
  PASS  F4-5 运行中：提示条仍在，R3 那句没被顶掉
  PASS  F4-6 运行中：多出一行运行说明
  PASS  F4-6 运行中：两段都在（不是二选一）
  PASS  F4-7 有结果：提示条仍在且可见
  PASS  F4-8 有结果：运行说明那一行收起来
  PASS  F4-9 收起后没有留下空的第二段
失败项：0
```

几何证据（`fix-round1-part2-f4.json`，`getBoundingClientRect` 实测）：

```json
"boot":    { "visible": true, "boxH": 55, "pCount": 2, "runningLine": { "hidden": true, "h": 0 } },
"running": { "visible": true, "boxH": 55, "pCount": 2, "runningLine": { "hidden": false } },
"settled": { "visible": true, "boxH": 55, "pCount": 2, "runningLine": { "hidden": true, "h": 0 } },
"geometry": { "boxH": 55, "paragraphs": [ { "text": "", "h": 0 }, { "text": "隔离不等于修复：被隔离的记录…", "h": 21 } ] }
```

首屏（`#/cleaning` 的默认入口）提示条 55px 高、写着「隔离不等于修复：被隔离的记录是移出正表…」；
运行中两段并存；有结果后运行那一段 `h=0`（`hidden` 真的不占位，没有留下空白横条）。
截图：`fix-round1-part2-f4-boot.png` / `-running.png` / `-settled.png`。

---

## 5. F5（Important）：总览运行中另两块没画占位 —— 已补

### 5.1 修法

`frontend/js/views/overview.js` 的空分支。旧代码 `both(this.explain, '', '')` / `both(this.limits, '', '')`：
传空串时 `both` 既不画说明、也不画占位（`else if (state.resultError)` 在这两块上不可能成立，
因为错误态已经在「数据量变化」里说完了），于是**面板整个是空的** —— 与上一行注释
"另两块保持空态的「—」"和 R50 的说明正好相反。

```js
      /* F5：这两块也要画一次占位 —— 之前传空串让它们**什么都不渲染**，面板整个是空的，
         与上面那句"另两块保持空态的「—」"和 R50 的说明正好相反。
         错误态不在这里重复第三遍（那是把同一条错误糊三次），只有空态与"有结果但缺内容"用「—」。 */
      volumes(note || guide, note ? 'running' : 'guide');
      for (const host of [this.explain, this.limits]) {
        clear(host);
        host.appendChild(el('p', 'void', '—'));
      }
```

（`both` 改名 `volumes` 并只服务「数据量变化」这一块：它的三个分支互斥、语义就是这一块的，
不需要"哪个 host 都行"的通用形参。）

### 5.2 实测

```
  PASS  F5-1 三块面板都有内容（没有空面板）
  PASS  F5-2 另两块各有一个「—」占位
  PASS  F5-3 「数据量变化」里是运行说明、不是「—」
失败项：0
```

```json
[ { "text": "任务正在「评分表清洗」阶段（3/9）。完成后这里会显示综合质量分、数据量变化和评价局限。",
    "voidCount": 0, "runningCount": 1 },
  { "text": "—", "voidCount": 1, "runningCount": 0 },
  { "text": "—", "voidCount": 1, "runningCount": 0 } ]
```

截图 `fix-round1-part2-f5-running.png`。

---

## 6. 四道门（全部跑在**提交后的 HEAD** 上）

### 6.1 单测

```
$ node --test "frontend/tests/*.test.mjs"
ℹ tests 90
ℹ pass 90
ℹ fail 0
```

### 6.2 对比度

```
$ node frontend/tools/check-contrast.mjs
全部通过：19 项
```

### 6.3 静态自检（含 §10.0 白屏守卫）

```
$ node frontend/tools/check.mjs
  PASS  无 color-mix() / field-sizing / text-wrap: balance（注释里提到不算）
  PASS  tokens.css 之外无裸 hex
  PASS  界面文案层无禁用词
  PASS  无 infinite 动画
  PASS  所有文件 ≤ 250 行
  PASS  banner-stack / timeline / health 在 HTML 里先存在
  PASS  31 个 JS 文件按 ESM 解析（node --check --input-type=module）
  PASS  31 个 JS 文件真 import 成功（语法 + 链接期）
静态自检全部通过
```

（31 = 30 + 新增的 `js/shell/tasks.js`。本轮每次改完 JS 都重跑了这一道。）

### 6.4 浏览器验收（真后台 + 真夹具，CDP + 真实点击）

```
$ node docs/frontend/plans/_sdd/drivers/browser-check-fix-round1.mjs http://localhost:8080/frontend/ docs/frontend/plans/_sdd
  … 28 项 PASS，失败项：0
$ node docs/frontend/plans/_sdd/drivers/browser-check-fix-round1-part2.mjs http://localhost:8080/frontend/ docs/frontend/plans/_sdd
  … 35 项 PASS，失败项：0
```

五个视图冒烟（真夹具 `20260929-190858-e13b2f`、真后端，每页零 `window.__errs`）：

```
$ node docs/frontend/plans/_sdd/drivers/smoke-views-fixr1.mjs
  lastTaskId = 20260929-190858-e13b2f
  PASS  #/overview  panels=3 正文=1291 字
  PASS  #/scores    panels=3 正文=677 字
  PASS  #/cleaning  panels=6 正文=2681 字 提示条=55px「隔离不等于修复：被隔离的记录是移出正表，不是被改」
  PASS  #/evidence  panels=2 正文=2849 字
  PASS  #/basis     panels=4 正文=3135 字
视图冒烟：五个视图全过
```

### 6.5 其它硬约束自查

| 约束 | 结论 |
|---|---|
| G1 零构建 / 零依赖 / 零 CDN / 零外部请求 | ✅ 新增只有一个 ESM 文件 `js/shell/tasks.js`，无 import 新依赖 |
| G5 `:root` 外无裸 hex、JS 无颜色字面量 | ✅ `check.mjs §10.3` PASS；本轮只往 cleaning.css 加了 3 条规则，全走 `var()` |
| G8 单文件 ≤ 250 行 | ✅ `check.mjs §10.13` PASS；最大 `taskbar.js` 241（余 9） |
| 橙色实心块同屏 ≤ 2 | ✅ 本轮没加任何实心橙块；`cleaning.css` 仍是 0 处（只有 `--accent-soft` 淡底与 2px 内轮廓） |
| `check.mjs:140` 禁 `animation: … infinite` | ✅ §10.12 PASS；本轮没加动画 |
| `tokens.css` 颜色值 | ✅ 零改动（`git diff --stat` 无此文件） |
| `ui/state.js` 只导出 `renderState` | ✅ 未动该文件；`withState` 全仓 0 命中 |
| 只动 `frontend/` 与 `docs/` | ✅ 见 §7 的 `git show --stat` |

---

## 7. 提交

一个提交，消息：

```
fix(frontend): 修「新任务」死锁与证据页残留报告，口径文案中性化，恢复 R3 常驻提示并补总览占位
```

显式 `git add` 的路径（**没有** `git add -A`）：

- `frontend/js/main.js`
- `frontend/js/shell/tasks.js`（新）
- `frontend/js/shell/taskbar.js`
- `frontend/js/views/evidence.js`
- `frontend/js/views/cleaning.js`
- `frontend/css/views/cleaning.css`
- `frontend/js/views/overview.js`
- `frontend/js/core/run-state.js`
- `docs/frontend/plans/_sdd/fix-round-round1-report.md`（本文件）

`frontend/tools/check.mjs` **没有改**（它本来就已经接上白屏守卫，§10.0 是上一轮的产物）。

---

## 8. 关切与遗留

1. **验收驱动自己踩了四个坑，都是"看着全绿其实没测到"的类型**（§1.3）。它们不改变交付代码，
   但会污染证据：`Fetch` 域跨导航、同 URL 同 hash 的同文档导航、装载期请求的拦截时机、
   自己 fulfill 的响应缺 CORS 头。已全部写进两个驱动的文件头注释。**建议下一轮的验收模板
   直接抄这两个驱动的 `netOff` / `netOnBeforeNav` / `fresh` 三件套**，别再摸索一遍。
2. **`taskbar.js` 只剩 2 行余量（248/250）**。本轮 F1 只加了 7 行（判据 + title + 赋值 + 判空 +
   一行注释），已经把 `render()` 的早退改成 `if/else` 来省行数、注释也压缩过。下一次还要动它，
   应该先拆 `.taskpick`（弹层 + 列表 + 淡出，约 90 行）到 `shell/taskpick.js`。
3. **`F1` 的判据散在两种写法里**：`tasks.isBusy()`（新）与 composer 里内联的
   `sending || !!s.liveTaskId || task.status === 'queued' || task.status === 'running'`（旧）。
   两处**现在**一致（运行中两者都为真、终态两者都为假，实测覆盖），但 composer 那份多了
   `liveTaskId` 与 `sending`。**没有改 composer**（本轮没有要求，且它贴着 B1 的裁定）。
   若下一轮要"一个真值来源"，应当让 composer 也调 `tasks.isBusy()` 并把 `liveTaskId`
   并进 `isBusy`；代价是「切换任务看历史结果」时发送键也会灰（现在不灰，因为那时不 busy）
   —— 语义要控制方裁定，我不擅自改。
4. **`resumeLastTask` 的时序被搬动了一点点**：搬迁前它是模块顶层的 IIFE（在 `router.start()`
   **之前**开始跑），现在是 `router.start()` **之后**的普通调用。两条路径都是"先发 `/status`、
   await 回来再 `store.set`"，而首个视图已经 mount 并自绘过一轮（R29/R12），所以实测（§1.2 的
   F1-7 与 §6.4 的冒烟）没有差别。写在这里是因为它确实是一处**非逐字**的搬迁。
5. **`docs/` 与验收驱动仍未入库**：本提交只含交付文件与本报告（显式路径）。两个驱动
   （`browser-check-fix-round1.mjs`、`-part2.mjs`、`smoke-views-fixr1.mjs`、`launch-edge.mjs`）、
   5 个 JSON、10 张截图与 3 个 txt 仍是未跟踪文件 —— 与批次 1–8、任务 12–14 的处理一致。
6. **真后端那两个任务是我起的**：`20261004-104643-b3ba4a`（成功提交、立刻因缺 `users.dat` 失败）
   与 `20261004-001640-923462`（先前已存在）。前者现在出现在「历史任务」列表里（失败、无结果），
   不影响任何验收断言。**没有删任务目录**（`var/tasks/` 不在我的授权范围内，
   它只是多了一行历史记录，属于 `agent/` 侧数据，未动。）
7. **F3 正文那句"评分标准与全量模式相同"的依据**是代码（`agent/tools.py:62` +
   `agent/agent.py:278-283`），不是文档。如果后端以后给 `sample` 换一套评分参数，这句话会变成
   错的 —— 届时必须连同判据一起改。已写进 `run-state.js` 的注释里。
