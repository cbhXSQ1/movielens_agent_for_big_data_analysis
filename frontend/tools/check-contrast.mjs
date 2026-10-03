/* 对比度自检：读 css/tokens.css，按 spec §5.1 核算 19 项。零依赖，只读文件。 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const cssPath = join(here, '..', 'css', 'tokens.css');

function lin(c) { const s = c / 255; return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); }
function lum(hex) {
  const h = hex.replace('#', '');
  return 0.2126 * lin(parseInt(h.slice(0, 2), 16))
       + 0.7152 * lin(parseInt(h.slice(2, 4), 16))
       + 0.0722 * lin(parseInt(h.slice(4, 6), 16));
}
export function ratio(a, b) {
  const x = lum(a), y = lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

const BGS = { bg: '#fdfdf8', surface: '#ffffff', 'surface-alt': '#f7f8f3', sunken: '#eeefe9' };

// [token, 承担哪个底, 门槛]
const TOKENS = [
  ['--fg', 'all', 4.5], ['--fg-2', 'all', 4.5], ['--fg-3', 'all', 4.5],
  ['--border-strong', 'noSunken', 3.0],
  ['--ok', 'all', 4.5], ['--warn', 'all', 4.5], ['--warn-graphic', 'all', 3.0],
  ['--danger', 'all', 4.5], ['--focus', 'bg', 3.0],
  ['--accent', 'all', 4.5], ['--accent-vivid', 'all', 3.0],
  ['--viz-before', 'sunken', 3.0], ['--viz-after', 'sunken', 3.0],
];
const PAIRS = [
  ['白字 on --accent', '#ffffff', '--accent', 4.5],
  ['白字 on --fg', '#ffffff', '--fg', 4.5],
  ['白字 on --ok', '#ffffff', '--ok', 4.5],
  ['白字 on --danger', '#ffffff', '--danger', 4.5],
  ['--border on 页面底', '--border', 'bg', 1.2],
  ['--viz-grid on 凹槽', '--viz-grid', 'sunken', 1.0],
];

const css = readFileSync(cssPath, 'utf8');
const declared = new Map();
for (const m of css.matchAll(/(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;/g)) {
  declared.set(m[1], m[2].toLowerCase());
}
function resolve(ref) { return ref.startsWith('#') ? ref : declared.get(ref); }

let fail = 0;
console.log('对比度核算（对四块底分别核算，取最差）\n');
for (const [tok, scope, need] of TOKENS) {
  const v = declared.get(tok);
  if (!v) { console.log(`  FAIL  ${tok} 未在 tokens.css 声明`); fail++; continue; }
  const keys = scope === 'all' ? Object.keys(BGS)
             : scope === 'noSunken' ? Object.keys(BGS).filter(k => k !== 'sunken')
             : [scope];
  const rs = keys.map(k => [k, ratio(v, BGS[k])]);
  const worst = rs.reduce((a, b) => (a[1] <= b[1] ? a : b));
  const ok = worst[1] >= need;
  if (!ok) fail++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${tok.padEnd(16)} ${v}  最差 ${worst[1].toFixed(2)} : 1（对 ${worst[0]}，需 ${need.toFixed(1)}）`);
}
console.log('');
for (const [label, fgRef, bgRef, need] of PAIRS) {
  const fg = resolve(fgRef), bg = BGS[bgRef] || resolve(bgRef);
  if (!fg || !bg) { console.log(`  FAIL  ${label} 无法解析`); fail++; continue; }
  const r = ratio(fg, bg);
  const ok = r >= need;
  if (!ok) fail++;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label.padEnd(22)} ${r.toFixed(2)} : 1（需 ${need.toFixed(1)}）`);
}
console.log('\n' + (fail === 0 ? '全部通过：19 项' : fail + ' 项未通过'));
process.exit(fail === 0 ? 0 : 1);
