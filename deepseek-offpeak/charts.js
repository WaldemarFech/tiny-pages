/*! tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages */
// Hand-written SVG charts (no libraries: strict CSP, no third-party scripts). All functions return an SVG string.
// Colors come from CSS classes (see router.css) so both themes work; shapes/dashes carry meaning, not color alone.

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function frame(w, h, inner, label) {
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="${esc(label)}" preserveAspectRatio="xMidYMid meet">${inner}</svg>`;
}

/** Line chart. series: [{name, cls, dash, points:[[x,y|null]...]}]. opts: w,h,yMin,yMax,yFmt,xFmt,xTicks,nowX,shadeFrom,label
 *  yMin (optional) zooms the y axis to the data range - for prices that all sit inside a narrow band, a 0-baseline flattens the
 *  differences the chart is supposed to show. The axis labels keep the absolute values, so nothing is exaggerated silently. */
export function lineChart(series, opts = {}) {
  const w = opts.w || 640, h = opts.h || 220;
  const m = { l: 46, r: 10, t: 12, b: 26 };
  const pts = series.flatMap((s) => s.points.filter((p) => p[1] !== null && p[1] !== undefined));
  if (!pts.length) return '';
  const xs = pts.map((p) => p[0]);
  const x0 = opts.xMin ?? Math.min(...xs), x1 = opts.xMax ?? Math.max(...xs);
  const yMax = opts.yMax ?? niceMax(Math.max(...pts.map((p) => p[1])));
  const yMin = opts.yMin ?? 0;
  const sx = (x) => m.l + ((x - x0) / (x1 - x0 || 1)) * (w - m.l - m.r);
  const sy = (y) => h - m.b - ((y - yMin) / (yMax - yMin || 1)) * (h - m.t - m.b);
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const y = yMin + ((yMax - yMin) * i) / 4;
    g += `<line class="grid" x1="${m.l}" x2="${w - m.r}" y1="${sy(y)}" y2="${sy(y)}"/><text class="axis" x="${m.l - 6}" y="${sy(y) + 4}" text-anchor="end">${esc((opts.yFmt || String)(y))}</text>`;
  }
  (opts.xTicks || []).forEach((t) => {
    g += `<text class="axis" x="${sx(t)}" y="${h - 8}" text-anchor="middle">${esc((opts.xFmt || String)(t))}</text>`;
  });
  if (opts.shadeFrom !== undefined) g += `<rect class="shade" x="${sx(opts.shadeFrom)}" y="${m.t}" width="${Math.max(0, w - m.r - sx(opts.shadeFrom))}" height="${h - m.t - m.b}"/>`;
  series.forEach((s) => {
    let d = '', pen = false;
    s.points.forEach(([x, y]) => {
      if (y === null || y === undefined) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${sx(x).toFixed(1)} ${sy(y).toFixed(1)}`;
      pen = true;
    });
    if (d) g += `<path class="line ${s.cls || ''}" d="${d}"${s.dash ? ` stroke-dasharray="${s.dash}"` : ''} fill="none"/>`;
    s.points.forEach(([x, y]) => {
      if (y !== null && y !== undefined && s.dots) g += `<circle class="dot ${s.cls || ''}" cx="${sx(x).toFixed(1)}" cy="${sy(y).toFixed(1)}" r="3"/>`;
    });
  });
  if (opts.nowX !== undefined) g += `<line class="now" x1="${sx(opts.nowX)}" x2="${sx(opts.nowX)}" y1="${m.t}" y2="${h - m.b}"/>`;
  return frame(w, h, g, opts.label || 'Liniendiagramm');
}

/** Grouped bars: cats [{label, a, b}] where a = expectation (outline), b = measurement (filled). opts: w,h,yFmt,label */
export function pairBars(cats, opts = {}) {
  const w = opts.w || 640, h = opts.h || 200;
  const m = { l: 46, r: 8, t: 12, b: 28 };
  const vals = cats.flatMap((c) => [c.a, c.b]).filter((v) => v !== null && v !== undefined);
  if (!vals.length) return '';
  const yMax = opts.yMax ?? niceMax(Math.max(...vals));
  const sy = (y) => h - m.b - (y / yMax) * (h - m.t - m.b);
  const slot = (w - m.l - m.r) / cats.length;
  const bw = Math.max(4, Math.min(26, slot * 0.38));
  let g = '';
  for (let i = 0; i <= 4; i++) {
    const y = (yMax * i) / 4;
    g += `<line class="grid" x1="${m.l}" x2="${w - m.r}" y1="${sy(y)}" y2="${sy(y)}"/><text class="axis" x="${m.l - 6}" y="${sy(y) + 4}" text-anchor="end">${esc((opts.yFmt || String)(y))}</text>`;
  }
  cats.forEach((c, i) => {
    const cx = m.l + slot * (i + 0.5);
    if (c.a !== null && c.a !== undefined) g += `<rect class="bar-exp" x="${(cx - bw - 1).toFixed(1)}" y="${sy(c.a).toFixed(1)}" width="${bw}" height="${Math.max(0, h - m.b - sy(c.a)).toFixed(1)}"/>`;
    if (c.b !== null && c.b !== undefined) g += `<rect class="bar-mea" x="${(cx + 1).toFixed(1)}" y="${sy(c.b).toFixed(1)}" width="${bw}" height="${Math.max(0, h - m.b - sy(c.b)).toFixed(1)}"/>`;
    if (cats.length <= 12 || i % Math.ceil(cats.length / 8) === 0) g += `<text class="axis" x="${cx}" y="${h - 9}" text-anchor="middle">${esc(c.label)}</text>`;
  });
  return frame(w, h, g, opts.label || 'Balkendiagramm Erwartung gegen Messung');
}

/** Horizontal bullet bars per provider: bar = measured, tick = expected. rows [{label, measured, expected}] values 0..1 */
export function bullets(rows, opts = {}) {
  const w = opts.w || 640, rowH = 34;
  const h = rows.length * rowH + 8;
  const m = { l: 128, r: 52 };
  let g = '';
  rows.forEach((r, i) => {
    const y = 6 + i * rowH;
    const sx = (v) => m.l + v * (w - m.l - m.r);
    g += `<text class="axis lbl" x="${m.l - 8}" y="${y + 17}" text-anchor="end">${esc(r.label)}</text><rect class="track" x="${m.l}" y="${y + 6}" width="${w - m.l - m.r}" height="14" rx="3"/>`;
    if (r.measured !== null && r.measured !== undefined) g += `<rect class="bar-mea" x="${m.l}" y="${y + 6}" width="${(sx(r.measured) - m.l).toFixed(1)}" height="14" rx="3"/><text class="axis val" x="${w - m.r + 6}" y="${y + 18}">${esc(r.valueText)}</text>`;
    if (r.expected !== null && r.expected !== undefined) g += `<line class="tick" x1="${sx(r.expected)}" x2="${sx(r.expected)}" y1="${y + 2}" y2="${y + 24}"/>`;
  });
  return frame(w, h, g, opts.label || 'Messung gegen Erwartung je Anbieter');
}
