/* 静态自检（spec §10 的自动化部分）。零依赖，只用 node: 内置模块，只读文件；
   它是开发期工具，**不参与运行时**（页面一行都不引用它）。

   跑法：node frontend/tools/check.mjs      任何一项 FAIL → 退出码 1。

   两条与本脚本有关的裁定：
   - R59（§10.4 禁用词表）：匹配前先「去掉行尾 `//…` 注释 + 跳过整行注释」，
     并豁免 js/views/basis.js —— spec §4.5 把 `#/basis` 定为术语白名单的落脚点，
     那里出现「口径」是要求，不是缺陷。
   - R50（§10.6「任务未完成时页面上数字个数 = 0」）：那是一条**浏览器**测量
     （可见数字、只在 .kpi / .panel__body 里数、排除 .state--error / .state--empty
     与 hidden 子树），静态读文件测不出来，所以本脚本不假装测它，只提示它在哪验。

   §10.1 对比度**不在本脚本里跑**：check-contrast.mjs 顶层直接 process.exit()，
   import 它会当场把本进程杀掉。单独跑它，输出以它自己为准（见文件末尾提示）。

   最后一道是**白屏守卫**（`§10.0`）：`js/**` 全部真 import 一遍。批次 B 踩过一次
   "一次 edit 吃掉换行、把行尾 `//` 注释和下一段块注释并成一行" → 语法错误 →
   整棵模块树 import 失败 → 页面全白、`window.__APP__` 是 undefined，
   而当时三道门全绿（90 个单测不 import 视图；`node --check` 对 ESM 是骗人的；
   check.mjs 自己也不 import）。所以这里补两道：逐文件 ESM 语法检查 + 真 import。 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join, extname } from 'node:path';
import { execFileSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const FE = join(here, '..');

function walk(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== 'tools' && name !== 'tests') walk(p, acc); }
    else acc.push(p);
  }
  return acc;
}

const files = walk(FE);
const read = p => readFileSync(p, 'utf8');
const rel = p => p.slice(FE.length + 1).replace(/\\/g, '/');

let fail = 0;
function check(label, bad) {
  const ok = bad.length === 0;
  if (!ok) fail++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${ok ? '' : '  → ' + bad.slice(0, 6).join(' | ')}`);
}

/* 把源码切成「只剩代码」的逐行文本：字符串原样保留，注释去掉。三条规则：
   - 块注释 `/* … *\/` 可以跨行，CSS / JS / HTML 都按状态机吃掉；
   - 行尾 `//…` 只有 JS 成立（CSS 的 `//` 不是注释）；
   - 字符串内部的 `//` 与 `/*` 不算注释 —— 否则 `'http://localhost:8765'`
     会被截断，同一行后面的中文文案就漏检了。
   用途：「有没有真的使用这个特性 / 这段文字」问的是代码与文案，注释是说明。
   §10.2 目前唯一的命中是 tokens.css:5 那句**禁用 color-mix() 的说明**。 */
function codeLines(text, lang) {
  const out = [];
  let inBlock = false;
  for (const raw of text.split(/\r?\n/)) {
    let line = '', i = 0, quote = null;
    while (i < raw.length) {
      const c = raw[i];
      if (inBlock) {
        if ((lang === 'html' && c === '-' && raw.slice(i, i + 3) === '-->') ||
            (lang !== 'html' && c === '*' && raw[i + 1] === '/')) { inBlock = false; i += lang === 'html' ? 3 : 2; }
        else i++;
        continue;
      }
      if (quote) {
        line += c;
        if (c === '\\') { line += raw[i + 1] || ''; i += 2; continue; }
        if (c === quote) quote = null;
        i++;
        continue;
      }
      if (c === "'" || c === '"' || c === '`') { quote = c; line += c; i++; continue; }
      if (lang === 'html' && raw.slice(i, i + 4) === '<!--') { inBlock = true; i += 4; continue; }
      if (lang !== 'html' && c === '/' && raw[i + 1] === '*') { inBlock = true; i += 2; continue; }
      if (lang === 'js' && c === '/' && raw[i + 1] === '/') break;
      line += c; i++;
    }
    out.push(line);
  }
  return out;
}

console.log('§10.2 兼容性禁令');
{
  const bad = [];
  const langOf = e => (e === '.js' ? 'js' : e === '.html' ? 'html' : 'css');
  for (const f of files.filter(f => ['.css', '.js', '.html'].includes(extname(f)))) {
    const lang = langOf(extname(f));
    codeLines(read(f), lang).forEach((line, i) => {
      for (const pat of ['color-mix(', 'field-sizing', 'text-wrap: balance']) {
        if (line.includes(pat)) bad.push(`${rel(f)}:${i + 1} ${pat}`);
      }
    });
  }
  check('无 color-mix() / field-sizing / text-wrap: balance（注释里提到不算）', bad);
}

console.log('§10.3 无裸颜色');
{
  const bad = [];
  for (const f of files) {
    const name = rel(f);
    if (name.endsWith('tokens.css')) continue;                 // token 的唯一容身处
    if (!['.css', '.js'].includes(extname(f))) continue;
    const lines = read(f).split(/\r?\n/);
    lines.forEach((line, i) => {
      if (/#ffffff|#fff\b/i.test(line) && /color:|background/.test(line)) return;  // components.css 的白字例外
      const m = line.match(/#[0-9a-fA-F]{3,6}\b/);
      if (m && !/fill="none"|color-mix/.test(line)) bad.push(`${name}:${i + 1} ${m[0]}`);
    });
  }
  check('tokens.css 之外无裸 hex', bad);
}

console.log('§10.4 禁用词表');
{
  const WORDS = ['口径', '兜底', '护栏', '红线', '纪律', '裁定', '如实交代', '不可用于汇报', '本次未产出', '正式口径'];
  /* spec §4.5 的白名单：术语只允许出现在 `#/basis` 与报告全文里。
     `#/basis` 那一句「口径差异说明」是 §4.5 明文要求保留的，扫到它是规定动作。 */
  const EXEMPT = new Set(['js/views/basis.js']);
  const bad = [];
  for (const f of files) {
    const name = rel(f);
    if (!/^js\/(views|shell)\//.test(name)) continue;          // 只查界面文案层
    if (EXEMPT.has(name)) continue;                            // 见上：§4.5 白名单
    const raw = read(f).split(/\r?\n/);
    const code = codeLines(raw.join('\n'), 'js');
    raw.forEach((line, i) => {
      if (/^\s*(\/\/|\*|\/\*)/.test(line)) return;             // R59：整行注释不算界面文案
      if (!code[i].trim()) return;                             // 块注释的续行（不带 `*` 前缀）也在注释里
      for (const w of WORDS) if (code[i].includes(w)) bad.push(`${name}:${i + 1} 「${w}」`);
    });
  }
  check('界面文案层无禁用词', bad);
}

console.log('§10.12 骨架屏不得无限循环');
{
  const bad = [];
  for (const f of files.filter(f => extname(f) === '.css')) {
    read(f).split(/\r?\n/).forEach((line, i) => {
      if (/animation[^;]*infinite/.test(line)) bad.push(`${rel(f)}:${i + 1}`);
    });
  }
  check('无 infinite 动画', bad);
}

console.log('§10.13 单文件行数 ≤ 250');
{
  const bad = [];
  for (const f of files.filter(f => ['.js', '.css', '.html'].includes(extname(f)))) {
    const n = read(f).split(/\r?\n/).length;
    if (n > 250) bad.push(`${rel(f)} = ${n} 行`);
  }
  check('所有文件 ≤ 250 行', bad);
}

console.log('§5.7 aria-live 容器先存在于 DOM');
{
  const html = read(join(FE, 'index.html'));
  const bad = [];
  for (const id of ['banner-stack', 'timeline', 'health']) {
    if (!html.includes(`id="${id}"`)) bad.push(`index.html 缺 #${id}`);
  }
  check('banner-stack / timeline / health 在 HTML 里先存在', bad);
}

console.log('\n只提示、不在这里跑的两项：');
console.log('  §10.1 对比度      → node frontend/tools/check-contrast.mjs（它自己会 process.exit，所以必须单独跑；期望「全部通过：19 项」）');
console.log('  §10.6 未完成不显示数字 → 浏览器测量（R50：可见数字 + 只在 .kpi/.panel__body 里数）');

/* ---- 白屏守卫：js/** 全部真 import 一遍（见文件头注释） ----
   两道，按"先静态后动态"排：
   ① 语法：`node --check --input-type=module -`，源码走 stdin。**不能**写成 `node --check <文件>` ——
      后者按 CommonJS 解析，`import`/`export` 会被静默跳过，语法错误根本抓不到（这就是批次 B 漏掉的原因）。
      写成 `--input-type=module -` 才是真的按 ESM 解析；一次一个子进程，报错行号不会串味。
   ② 链接 + 求值：真 `import()` 每个文件。它同时兜住 ①（被子模块的语法错误打断）与
      "import 了不存在的绑定"这类**链接期**错误 —— 迭代一有过 `import { withState }` 的先例。
      本脚本自己**不** import 业务模块（就地 import 会让视图的手写 DOM 跑在 Node 里），
      所以放在**子进程**里跑：`node --input-type=module -e <脚本>`，没有 `--experimental-*`、
      没有临时文件、不污染本进程的全局。子进程里只给最小 DOM 壳（`document` 等）；
      加壳的唯一目的就是让模块**能求值到底**，从而把链接期错误暴露出来。 */
console.log('\n§10.0 白屏守卫（js/** 真 import 一遍）');
{
  const jsFiles = files.filter(f => extname(f) === '.js');
  const syntaxBad = new Map();
  for (const f of jsFiles) {
    try { execFileSync(process.execPath, ['--check', '--input-type=module', '-'], { input: read(f), stdio: ['pipe', 'ignore', 'pipe'] }); }
    catch (e) {
      syntaxBad.set(f, String((e.stderr && e.stderr.toString()) || '').trim().split('\n').slice(0, 3).join(' '));
    }
  }
  check(`${jsFiles.length} 个 JS 文件按 ESM 解析（node --check --input-type=module）`,
    [...syntaxBad].map(([f, msg]) => `${rel(f)} → ${msg}`));

  const SHIM = `const stub = () => { const o = { style: { setProperty() {}, removeProperty() {} },
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false }, dataset: {}, children: [], childNodes: [], firstChild: null,
    value: '', textContent: '', innerHTML: '', className: '', id: '', hidden: false,
    appendChild: c => c, removeChild: c => c, insertBefore: c => c, remove() {},
    setAttribute() {}, getAttribute: () => null, removeAttribute() {}, hasAttribute: () => false,
    addEventListener() {}, removeEventListener() {}, focus() {}, blur() {}, click() {},
    getBoundingClientRect: () => ({ top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }) };
    return new Proxy(o, { get: (t, k) => (k in t ? t[k] : (k === 'querySelector' || k === 'querySelectorAll' || k === 'closest' ? () => stub() : undefined)), set: (t, k, v) => (t[k] = v, true) }); };
  globalThis.document = new Proxy({ createElement: () => stub(), createElementNS: () => stub(),
    getElementById: () => stub(), documentElement: stub(), head: stub(), body: stub(),
    addEventListener() {}, removeEventListener() {}, readyState: 'complete', fonts: { addEventListener() {} } },
    { get: (t, k) => (k in t ? t[k] : () => stub()) });
  globalThis.window = new Proxy({ print() {}, addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    location: { hash: '', href: 'http://localhost:8080/frontend/' }, innerWidth: 1440, innerHeight: 900 },
    { get: (t, k) => (k in t ? t[k] : () => stub()) });
  globalThis.location = globalThis.window.location;
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {}, clear() {} };
  globalThis.setInterval = () => 0; globalThis.clearInterval = () => {};
  globalThis.requestAnimationFrame = () => 0;
  globalThis.fetch = async () => ({ ok: false, json: async () => ({}) });`;
  const IMPORT = `const urls = JSON.parse(process.argv[1]);
  let bad = 0;
  for (const u of urls) { try { await import(u); } catch (e) { bad++; console.log('BAD ' + u + ' :: ' + e.constructor.name + ': ' + String(e.message).split('\\n')[0]); } }
  process.exit(bad ? 1 : 0);`;

  const importBad = [];
  try {
    execFileSync(process.execPath,
      ['--input-type=module', '-e', SHIM + '\n' + IMPORT, JSON.stringify(jsFiles.map(f => pathToFileURL(f).href))],
      { stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    const lines = String((e.stdout && e.stdout.toString()) || '').split('\n').filter(l => l.startsWith('BAD '));
    const tail = String((e.stderr && e.stderr.toString()) || '').trim().split('\n')[0];
    if (!lines.length) importBad.push('子进程没能跑起来：' + (tail || e.message));
    for (const l of lines) {
      const url = l.slice(4, l.indexOf(' :: '));
      const f = jsFiles.find(x => pathToFileURL(x).href === url);
      /* 语法错误已经在上一道报过，这里不重复计数，只报"能解析但连不起来 / 求值就炸"的。 */
      if (f && !syntaxBad.has(f)) importBad.push(`${rel(f)} → ${l.slice(l.indexOf(' :: ') + 4)}`);
    }
  }
  check(`${jsFiles.length} 个 JS 文件真 import 成功（语法 + 链接期）`, importBad);
}

console.log('\n' + (fail === 0 ? '静态自检全部通过' : fail + ' 项未通过'));
process.exit(fail === 0 ? 0 : 1);
