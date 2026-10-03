# MovieLens 1M 数据治理控制台 · 前端

这是「Agent 驱动的 Hadoop 数据清洗与五维质量评估」的网页界面：用户在左边说一句中文，
Agent 把它变成一次工具调用，Hadoop 真的跑一遍清洗与评分，右边五个视图展示结果。

**零构建、零依赖、零 CDN、零外部请求。** 原生 HTML + CSS + ES Module，
图表是手写内联 SVG，没有打包器、没有框架、没有外部字体与图标库。
页面运行时不依赖 Node —— Node 只在开发期用来跑测试与自检脚本。

---

## 1. 怎么跑

页面要同时访问两样东西：

| 要访问的东西 | 地址 | 说明 |
|---|---|---|
| Agent HTTP 接口 | `http://localhost:8765` | 由 `agent/http_api.py` 提供 |
| 配置文件 | `/config/*.json` | 「依据」页要读，必须与页面同源 |

所以是两条命令、两个终端。

### 第 1 步：起后端（终端 A）

```powershell
cd <仓库根目录>
$env:PYTHONIOENCODING='utf-8'; python -m agent.http_api --port 8765
```

看到监听即成功。`GET http://127.0.0.1:8765/health` 应返回 `{"ok":true,…}`。
没起也不影响打开页面 —— 顶栏会显示「后端未连接」，并给出一条红色提示。

### 第 2 步：起静态服务（终端 B，**必须在仓库根目录**）

```powershell
cd <仓库根目录>
python -m http.server 8080
```

然后浏览器打开：

```
http://localhost:8080/frontend/
```

> **为什么静态服务必须在仓库根目录？**
> 「依据」页要读 `/config/*.json`。从仓库根目录起服务时，页面在 `/frontend/`、配置在 `/config/`，
> 属于同源，直接读得到。如果改在 `frontend/` 目录里起服务，配置就取不到 ——
> 页面会如实显示取数失败，而不是把评分方案硬编码进 JavaScript。

页面只跟这两个本地地址说话，不会向任何外部域名发请求（见第 5 节）。

---

## 2. 目录结构

```
frontend/
├── index.html            页面外壳：顶栏 / 任务条 / 横幅位 / 左栏 / 视图区 / 弹窗挂点
├── css/
│   ├── tokens.css        设计 token 的唯一来源（颜色、字号、间距、圆角、阴影、动效）
│   ├── base.css          重置 + 中文字体与行高 + 焦点环 + 工具类
│   ├── shell.css         顶栏 / 任务条 / 视图导航 / 分隔条 / 左栏 / 输入条
│   ├── components.css    面板 / KPI / 表格 / 徽标 / 横幅 / 弹窗 / 步骤流 / 状态容器
│   └── views/            五个视图各自的样式，进视图时才加载
├── js/
│   ├── main.js           入口：装配状态源、接口、路由与外壳；视图注册表在这里
│   ├── core/             不碰 DOM 的地基
│   │   ├── api.js        唯一的 HTTP 通道（错误码 → 中文）
│   │   ├── store.js      唯一状态源 + 订阅
│   │   ├── config.js     同源读 config/*.json（带缓存与失败态）
│   │   ├── format.js     数字 / 千分位 / 时长 / 阶段名，所有数字都过这里
│   │   ├── report.js     把一次运行渲染成可打印的报告
│   │   ├── run-state.js  判断这一轮是集群还是本地、全量还是抽样
│   │   └── dom.js        建元素 / 清空 / 安全插入
│   ├── shell/            外壳零件：router / taskbar / composer / banners / timeline / splitter / settings
│   ├── ui/               原语：panel / kpi / table / radar / bars / steps / state
│   └── views/            五个视图：overview / scores / cleaning / evidence / basis
├── tests/                12 个单元测试文件（89 项断言）
└── tools/                开发期脚本，不参与运行时（见第 3 节）
```

### 加一个视图 = 3 个文件

1. 新建 `js/views/<名字>.js`，默认导出一个对象：

   ```js
   export default {
     id: 'scores', title: '五维', order: 20,
     icon: '<svg …></svg>',        // 内联 SVG，零请求
     css: 'css/views/scores.css',  // 首次进入时自动注入 <link>
     mount(root, ctx) { /* 建骨架、绑事件，只在进入时跑一次 */ },
     update(state)    { /* 数据变了重画，必须幂等 */ },
   };
   ```

2. 新建 `css/views/<名字>.css`。
3. 在 `js/main.js` 顶部 `import` 它，并加进 `VIEWS` 数组。

导航栏、路由、样式加载都从 `VIEWS` 生成，不用再改别处。改一个视图只动它自己那两个文件。

---

## 3. 开发期命令

```powershell
node --test "frontend/tests/*.test.mjs"   # 单元测试（89 项）
node frontend/tools/check.mjs             # 静态自检（禁用写法 / 裸颜色 / 界面用词 / 文件行数 / 动画）
node frontend/tools/check-contrast.mjs    # 对比度核算（19 项）
```

三条都要求退出码 0。

> **测试必须用带引号的 glob 形式。** 本机 Node 是 v24，它的 test runner 把位置参数当
> glob 处理；写 `node --test frontend/tests/`（字面目录）会把目录当脚本执行，
> 报 `Cannot find module '…\frontend\tests'` 并退出 1。单文件形式
> （`node --test frontend/tests/format.test.mjs`）不受影响。

两个自检脚本都只读文件，改完代码跑一遍即可；它们不参与页面运行，页面一行都不引用它们。

---

## 4. 设计 token 与两条取舍

**`css/tokens.css` 是颜色、字号、间距、圆角、阴影、动效的唯一来源。**
其他地方不写裸颜色值（`#rrggbb`），JavaScript 里也不写颜色字面量 ——
图表需要颜色时读 CSS 变量。字号只有 7 档（28 / 19 / 15 / 13.5 / 12.5 / 11.5 / 11 px），
字重只有 3 档（400 / 600 / 700，700 只给大号数字），间距只有 8 档，圆角只有卡面 6px、
控件 4px、胶囊 999px。

### 中文为什么只用系统无衬线

目标机器是 Ubuntu 22.04 虚拟机，默认只装 `fonts-noto-cjk`（只有无衬线，没有中文衬线），
而且不允许引用外部字体。所以中文只声明系统无衬线栈
（`PingFang SC` / `Hiragino Sans GB` / `Microsoft YaHei` / `Source Han Sans SC` /
`Noto Sans CJK SC` / `WenQuanYi Micro Hei` / `sans-serif`），落到哪台机器上都是黑体一类，
不会是浏览器默认的衬线回退。中文行高用正文 1.7、标题 1.35（拉丁字体常见的 1.5 / 1.17
会让多行中文上下相碰），中文字距为 0；只有 11.5px 的小号标签与表头用 `.02em`
（负字距会把中文字形挤坏）。

### 为什么禁用 `color-mix()` 这一类新特性

目标环境的浏览器版本不确定，`color-mix()`（Chrome 111 / Firefox 113 起）、
CSS 嵌套、`field-sizing`、`text-wrap: balance` 在老一点的浏览器上会**整条声明失效**，
颜色和排版会直接掉回默认值。所以颜色一律在 `tokens.css` 里预先算成静态值，
运行时不做混色；这四条在 `check.mjs` 里逐条扫，命中即失败。
`:has()` 只用在一处（打印时只留报告区），支持它的浏览器上都正常，不支持时会连别的面板一起打印。

---

## 5. 页面上的数字从哪来

- 数字只从 `/api/tasks/{id}/result` 与 `/api/tasks/{id}/samples` 取，不从对话文本里解析；
  大模型只负责把话理解成一次工具调用，它不产生任何数字，也读不到任何数字。
- **任务没有成功时，结果区一个数字都不显示**，只显示状态；接口说「还没跑完」时页面继续显示进度，
  绝不拿上一轮的旧数字顶替。缺失的值显示「—」，不记 0。
  （「依据」页上还会有数字，那是配置里的权重与规则编号，不是本次运行的结果。）
- 清洗规则、阈值、权重、指标名、五维与指标的对应关系，全部从 `config/*.json` 或接口读，
  代码里不写这些内容。
- 「本地引擎」跑出来的结果没有发布到 HDFS，页面会用黄色横幅写明这一点；
  成功走 Hadoop 集群时是绿色横幅。
- **页面只向上面那两个本地地址发请求**：整页冷加载实测 36 个响应，主机只有
  `127.0.0.1:8080`（静态服务）与 `localhost:8765`（后端），0 个失败请求、0 个外部域名。
  后端没起时只有 `/health` 与那一次 `/status` 会失败，页面用红色横幅说明并回到空态首屏。

---

## 6. 已知限制

| # | 限制 | 说明 |
|---|---|---|
| 1 | **没有取消任务的入口** | 后端与 Agent 都没有取消接口，所以页面不画一个点了没用的按钮，改为显示当前阶段、进度与已用时。 |
| 2 | **没有暗色模式** | 只有一套浅色配色。颜色都收在 token 里，将来加是一段 `:root` 覆盖加一份对比度核算。 |
| 3 | **没有独立报告页** | 只有单页外壳。「证据」页可以加载完整报告、复制、下载并打印成 PDF（打印只输出报告区）。 |
| 4 | **重载后显示「本次的运行设置未知」** | `/status` 与 `/api/tasks` 都不返回这一轮的运行参数（全量还是抽样、集群还是本地）。页面刷新后接回任务、或从「切换任务」打开历史任务时拿不到它们，于是显示灰色提示「本次的运行设置未知」，也不显示全量或抽样横幅 —— 宁可少说，不错说。 |
| 5 | **两个视图没有单元测试** | `views/overview.js` 与 `views/scores.js` 没有测试文件；`core/dom.js`、`ui/panel.js`、`ui/kpi.js`、`ui/state.js` 与 `shell/` 里 `taskbar / composer / banners / timeline / settings` 也没有。单元测试覆盖的是 `core/` 与 `ui/` 的纯逻辑，加上 `router` 与 `splitter`（12 个文件、89 项）；视图和那几个零件靠真浏览器走查。 |
| 6 | **取数期间没有加载提示** | 请求还在飞的时候，面板是空的（或只有一个「—」），没有骨架屏或加载文案；数据到了直接出现。任务进度只在任务条上显示。 |
| 7 | **失败任务的原因暂时看不到** | 任务条会显示「失败」，但失败原因与错误 ID 没有展示入口（`/status` 里的 `errors` 取到了却没画出来）。另外刷新页面时失败的任务不会被接回来 —— 页面显示「还没有任务」，要自己从「切换任务」里找回来。 |
| 8 | **结果取不到时，总览与五维只有「—」** | `/result` 取不到时，总览与五维不会说明失败原因（「证据」与「清洗」会如实显示取数失败的原因）。 |
