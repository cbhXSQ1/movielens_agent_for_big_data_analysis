/* =============================================================================
   charts.js —— 手写内联 SVG 图表（零依赖、零 CDN，离线可用）
   -----------------------------------------------------------------------------
   为什么手写：运行环境可能完全离线，不允许任何外部图表库。
   全部图表遵守设计契约：
     · 平面、无渐变、无阴影、无装饰
     · 网格线 1px，用 --viz-grid；坐标文字 Gray 70
     · 数字等宽（tabular-nums），千分位分隔
     · 清洗前 = 中性灰，清洗后 = 语义绿；不吃「唯一强调色」的配额
   每个 SVG 都带 role="img" 与 <title>/<desc>，屏幕阅读器可读；
   同样的数据在页面上另有表格，不以颜色作为唯一信息载体。
   ========================================================================== */

(function (global) {
  'use strict';

  var C = {
    text:   '#525252',
    muted:  '#6f6f6f',
    grid:   '#e0e0e0',
    axis:   '#8d8d8d',
    before: '#8d8d8d',
    after:  '#24a148',
    warning:'#f1c21b'
  };

  /* ---------------------------------------------------------------- 格式化 */

  var fmt = {
    /** 1234567 → "1,234,567" */
    int: function (n) {
      if (n === null || n === undefined || isNaN(n)) return '—';
      return Number(n).toLocaleString('en-US');
    },
    /** 保留两位小数；null/undefined → "—"（绝不把「没数据」画成 0） */
    num: function (n, digits) {
      if (n === null || n === undefined || n === '' || isNaN(n)) return '—';
      var d = (digits === undefined) ? 2 : digits;
      return Number(n).toFixed(d);
    },
    /** 带正负号的变化值 */
    delta: function (n, digits) {
      if (n === null || n === undefined || n === '' || isNaN(n)) return '—';
      var d = (digits === undefined) ? 2 : digits;
      var v = Number(n);
      var s = Math.abs(v).toFixed(d);
      if (v > 0) return '+' + s;
      if (v < 0) return '−' + s;   // U+2212 减号，比连字符更清楚
      return '0.' + new Array(d + 1).join('0');
    },
    /** 百分比（0–1 → "42.00%"）。这里只是格式示例，本文件不得出现任何真实运行结果值。 */
    pct: function (n, digits) {
      if (n === null || n === undefined || isNaN(n)) return '—';
      return Number(n).toFixed(digits === undefined ? 2 : digits) + '%';
    }
  };

  /* ---------------------------------------------------------------- 工具 */

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function round(n) { return Math.round(n * 100) / 100; }

  /** 数值 → 文本；缺失值返回 "—" 而不是 0 */
  function label(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return Number(v).toFixed(digits === undefined ? 2 : digits);
  }

  /* ---------------------------------------------------------------- 雷达图 */

  /**
   * 五维雷达（两条多边形叠加）。
   * opts = {
   *   axes:   [{key, label}],
   *   series: [{name, color, fillColor, values:[数字|null]}],
   *   max:    100,
   *   size:   380,
   *   ariaLabel: '...'
   * }
   * 缺失值（null）会被当作 0 参与画图，但在图例里会被标注 —— 调用方负责提示。
   */
  function radar(opts) {
    var axes = opts.axes || [];
    var series = opts.series || [];
    var n = axes.length;
    if (!n) return '';

    var size = opts.size || 380;
    var cx = size / 2, cy = size / 2 + 4;
    var r = size / 2 - 62;                       // 留出中文标签空间
    var max = opts.max || 100;
    var rings = 4;

    function point(i, ratio) {
      var a = -Math.PI / 2 + (i * 2 * Math.PI / n);
      return [cx + Math.cos(a) * r * ratio, cy + Math.sin(a) * r * ratio];
    }

    var out = [];
    out.push('<svg viewBox="0 0 ' + size + ' ' + (size + 8) + '" width="100%" height="auto" ' +
             'role="img" aria-label="' + esc(opts.ariaLabel || '五维雷达图') + '" ' +
             'style="display:block;max-width:' + size + 'px;margin:0 auto">');
    out.push('<title>' + esc(opts.ariaLabel || '五维雷达图') + '</title>');

    // 同心网格 + 轴线
    for (var g = 1; g <= rings; g++) {
      var pts = [];
      for (var i = 0; i < n; i++) { var p = point(i, g / rings); pts.push(round(p[0]) + ',' + round(p[1])); }
      out.push('<polygon points="' + pts.join(' ') + '" fill="none" stroke="' + C.grid + '" stroke-width="1"/>');
    }
    for (var j = 0; j < n; j++) {
      var e = point(j, 1);
      out.push('<line x1="' + round(cx) + '" y1="' + round(cy) + '" x2="' + round(e[0]) + '" y2="' + round(e[1]) +
               '" stroke="' + C.grid + '" stroke-width="1"/>');
    }
    // Y 轴刻度（沿正上方轴，标 0/50/100）
    [0, 0.5, 1].forEach(function (ratio) {
      var y = cy - r * ratio;
      out.push('<text x="' + round(cx + 6) + '" y="' + round(y - 2) + '" font-size="10" fill="' + C.muted +
               '" font-family="monospace">' + Math.round(max * ratio) + '</text>');
    });

    // 数据多边形
    series.forEach(function (s) {
      var pts = [];
      for (var k = 0; k < n; k++) {
        var v = s.values[k];
        var ratio = (v === null || v === undefined || isNaN(v)) ? 0 : Math.max(0, Math.min(1, v / max));
        var q = point(k, ratio);
        pts.push(round(q[0]) + ',' + round(q[1]));
      }
      out.push('<polygon points="' + pts.join(' ') + '" fill="' + esc(s.fillColor || 'none') +
               '" stroke="' + esc(s.color) + '" stroke-width="2" stroke-linejoin="miter"/>');
      // 顶点
      for (var m = 0; m < n; m++) {
        var vv = s.values[m];
        var rr = (vv === null || vv === undefined || isNaN(vv)) ? 0 : Math.max(0, Math.min(1, vv / max));
        var pp = point(m, rr);
        out.push('<rect x="' + round(pp[0] - 2.5) + '" y="' + round(pp[1] - 2.5) + '" width="5" height="5" fill="' +
                 esc(s.color) + '"/>');
      }
    });

    // 轴标签（中文维度名）
    for (var t = 0; t < n; t++) {
      var a2 = -Math.PI / 2 + (t * 2 * Math.PI / n);
      var lx = cx + Math.cos(a2) * (r + 20);
      var ly = cy + Math.sin(a2) * (r + 20);
      var anchor = 'middle';
      if (Math.cos(a2) > 0.3) anchor = 'start';
      else if (Math.cos(a2) < -0.3) anchor = 'end';
      out.push('<text x="' + round(lx) + '" y="' + round(ly + 4) + '" text-anchor="' + anchor +
               '" font-size="12" fill="' + C.text + '">' + esc(axes[t].label) + '</text>');
    }

    out.push('</svg>');
    return out.join('');
  }

  /* ------------------------------------------------------------ 分组条形图 */

  /**
   * 每个维度一行，前/后两根条（同一 0–max 标尺，可直接比长短）。
   * opts = {
   *   rows:   [{label, before, after}],     // 数值可为 null → 画空并标 "—"
   *   max:    100,
   *   width:  560,
   *   rowH:   46,
   *   nameBefore: '清洗前', nameAfter: '清洗后',
   *   ariaLabel: '...'
   * }
   */
  function groupedBars(opts) {
    var rows = opts.rows || [];
    var max = opts.max || 100;
    var width = opts.width || 560;
    var rowH = opts.rowH || 46;
    var labelW = 92;                 // 左侧维度名
    var valueW = 118;                // 右侧数值
    var trackW = width - labelW - valueW;
    var height = rows.length * rowH + 8;
    if (!rows.length) return '';

    var out = [];
    out.push('<svg viewBox="0 0 ' + width + ' ' + height + '" width="100%" height="auto" role="img" ' +
             'aria-label="' + esc(opts.ariaLabel || '维度前后对比') + '" style="display:block">');
    out.push('<title>' + esc(opts.ariaLabel || '维度前后对比') + '</title>');

    // 竖直网格（0 / 50% / 100%）
    [0, 0.5, 1].forEach(function (ratio) {
      var x = round(labelW + trackW * ratio);
      out.push('<line x1="' + x + '" y1="0" x2="' + x + '" y2="' + (rows.length * rowH) +
               '" stroke="' + C.grid + '" stroke-width="1"/>');
    });

    rows.forEach(function (row, i) {
      var top = i * rowH;
      var barH = 9;
      var gap = 5;

      out.push('<text x="0" y="' + round(top + 20) + '" font-size="12" fill="' + C.text + '">' +
               esc(row.label) + '</text>');

      var vals = [
        { v: row.before, color: C.before, y: top + 14 },
        { v: row.after,  color: C.after,  y: top + 14 + barH + gap }
      ];
      vals.forEach(function (item) {
        var ratio = (item.v === null || item.v === undefined || isNaN(item.v))
          ? null : Math.max(0, Math.min(1, item.v / max));
        // 轨道底
        out.push('<rect x="' + labelW + '" y="' + round(item.y) + '" width="' + round(trackW) + '" height="' + barH +
                 '" fill="#f4f4f4"/>');
        if (ratio !== null && ratio > 0) {
          out.push('<rect x="' + labelW + '" y="' + round(item.y) + '" width="' + round(trackW * ratio) +
                   '" height="' + barH + '" fill="' + item.color + '"/>');
        }
        out.push('<text x="' + (width) + '" y="' + round(item.y + barH - 1) +
                 '" text-anchor="end" font-size="12" font-family="monospace" fill="' + C.text + '">' +
                 esc(label(item.v, 2)) + '</text>');
      });
    });

    out.push('</svg>');
    return out.join('');
  }

  /* ------------------------------------------------------- 输入→输出对比条 */

  /**
   * 三表数据量变化：每表一行，「输入(灰) → 输出(绿)」两条等长标尺。
   * opts = { rows:[{label, input, output}], width, rowH, ariaLabel }
   */
  function volumeBars(opts) {
    var rows = opts.rows || [];
    if (!rows.length) return '';
    var width = opts.width || 560;
    var rowH = opts.rowH || 52;
    var labelW = 76, valueW = 150;
    var trackW = width - labelW - valueW;
    var max = 0;
    rows.forEach(function (r) { max = Math.max(max, Number(r.input) || 0, Number(r.output) || 0); });
    if (max <= 0) return '';
    var height = rows.length * rowH + 4;

    var out = [];
    out.push('<svg viewBox="0 0 ' + width + ' ' + height + '" width="100%" height="auto" role="img" ' +
             'aria-label="' + esc(opts.ariaLabel || '数据量变化') + '" style="display:block">');
    out.push('<title>' + esc(opts.ariaLabel || '数据量变化') + '</title>');

    rows.forEach(function (row, i) {
      var top = i * rowH;
      var barH = 9, gap = 5;
      out.push('<text x="0" y="' + round(top + 20) + '" font-size="12" fill="' + C.text + '">' +
               esc(row.label) + '</text>');

      var pair = [
        { v: row.input,  color: C.before, y: top + 14, tag: '输入' },
        { v: row.output, color: C.after,  y: top + 14 + barH + gap, tag: '输出' }
      ];
      pair.forEach(function (item) {
        var v = Number(item.v) || 0;
        var ratio = Math.max(0, Math.min(1, v / max));
        out.push('<rect x="' + labelW + '" y="' + round(item.y) + '" width="' + round(trackW) + '" height="' + barH +
                 '" fill="#f4f4f4"/>');
        if (ratio > 0) {
          out.push('<rect x="' + labelW + '" y="' + round(item.y) + '" width="' + round(trackW * ratio) +
                   '" height="' + barH + '" fill="' + item.color + '"/>');
        }
        out.push('<text x="' + width + '" y="' + round(item.y + barH - 1) +
                 '" text-anchor="end" font-size="12" font-family="monospace" fill="' + C.text + '">' +
                 esc(fmt.int(item.v)) + '</text>');
      });
    });

    out.push('</svg>');
    return out.join('');
  }

  /* ------------------------------------------------------------- 迷你条形 */

  /**
   * 单值横向条（用于隔离规则排行等内联场景）。
   * opts = { value, max, color, width, height, ariaLabel }
   */
  function miniBar(opts) {
    var width = opts.width || 120;
    var height = opts.height || 10;
    var ratio = (opts.max > 0) ? Math.max(0, Math.min(1, (Number(opts.value) || 0) / opts.max)) : 0;
    return '<svg viewBox="0 0 ' + width + ' ' + height + '" width="' + width + '" height="' + height +
           '" role="img" aria-label="' + esc(opts.ariaLabel || '') + '" style="display:block">' +
           '<rect x="0" y="0" width="' + width + '" height="' + height + '" fill="#e0e0e0"/>' +
           '<rect x="0" y="0" width="' + round(width * ratio) + '" height="' + height +
           '" fill="' + esc(opts.color || C.before) + '"/></svg>';
  }

  global.ODFmt = fmt;
  global.ODCharts = {
    COLORS: C,
    esc: esc,
    radar: radar,
    groupedBars: groupedBars,
    volumeBars: volumeBars,
    miniBar: miniBar
  };
})(window);
