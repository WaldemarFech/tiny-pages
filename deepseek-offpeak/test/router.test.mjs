import test from 'node:test';
import assert from 'node:assert/strict';

import { ago, breakevenHit, dayTs, errorNotice, extLink, orModelUrl, orRankingsUrl, safeOrUrl, describeFlank, entryUnitCost, fmtPct, fmtTok, fmtUsd, fmtUsd2, pickTop3, statusOf, unitCost, verdict, whatIf } from '../model.js';
import { renderFree, renderMarket } from '../trending.js';
import { bullets, lineChart, pairBars } from '../charts.js';
import { explain } from '../explain.js';

const MORPH = { tag: 'morph/fp8', quant: 'fp8', in: 0.022, out: 0.192, cr: 0.004 };
const OI = { tag: 'open-inference/fp4', quant: 'fp4', in: 0.0141, out: 0.144, cr: 0.013 };
const RELACE = { tag: 'relace/fp4', quant: 'fp4', in: 0.035, out: 0.5, cr: 0.005 };

test('cost model matches the Python router: cache-read price dominates at high hit rates', () => {
  const prof = { hit: 0.937, out_ratio: 0.0025 };
  assert.ok(unitCost(MORPH, prof) < unitCost(RELACE, prof));
  assert.ok(unitCost(RELACE, prof) < unitCost(OI, prof));
  assert.ok(Math.abs(unitCost(MORPH, prof) - 0.005613) < 1e-5);
});

test('whatIf ranks live and flips when the cache hit rate drops', () => {
  const hi = whatIf([OI, MORPH, RELACE], { tokensPerDay: 1e9, hit: 0.94, outRatio: 0.0025 });
  assert.equal(hi[0].tag, 'morph/fp8');
  assert.equal(hi[0].delta, 0);
  const lo = whatIf([OI, MORPH, RELACE], { tokensPerDay: 1e9, hit: 0.3, outRatio: 0.0025 });
  assert.equal(lo[0].tag, 'open-inference/fp4');
  assert.ok(Math.abs(hi[0].usd - (1e9 * unitCost(MORPH, { hit: 0.94, out_ratio: 0.0025 })) / 1e6) < 1e-9);
});

test('breakeven cache hit between Open Inference and Morph is near 47 %', () => {
  const h = breakevenHit(OI, MORPH, 0);
  assert.ok(h > 0.45 && h < 0.5, String(h));
  assert.equal(breakevenHit(MORPH, { ...MORPH }, 0), null);
});

test('formatters use German number format and handle missing values', () => {
  assert.equal(fmtUsd(null), '–');
  assert.equal(fmtUsd(0.0012), '$0,0012');
  assert.equal(fmtUsd(2.5), '$2,50');
  // billed amounts keep 2 decimals even above $10 (fmtUsd switches to 1 there)
  assert.equal(fmtUsd2(31.728), '$31,73');
  assert.equal(fmtUsd2(17.31), '$17,31');
  assert.equal(fmtUsd2(-0.42), '$-0,42');
  assert.equal(fmtUsd2(null), '–');
  assert.equal(fmtTok(1538000000), '1,54 Mrd');
  assert.equal(fmtTok(84619), '85 k');
  assert.equal(fmtPct(0.9366, 1), '93,7 %');
  assert.equal(ago(100, 100 + 3 * 3600), 'vor 3 h');
  assert.equal(ago(null), 'nie');
});

test('status badges always carry a word and a symbol, not only color', () => {
  for (const s of ['ok', 'watch', 'off', 'na']) {
    const m = statusOf({ status: s });
    assert.ok(m.label.length > 1 && m.icon.length >= 1, s);
  }
  assert.equal(statusOf(undefined).cls, 'na');
});

test('verdict summarizes the four badges', () => {
  assert.equal(verdict({ a: { status: 'na' }, b: { status: 'na' } }).cls, 'na');
  assert.equal(verdict({ a: { status: 'ok' }, b: { status: 'na' } }).cls, 'ok');
  assert.equal(verdict({ a: { status: 'ok' }, b: { status: 'watch' } }).cls, 'watch');
  assert.equal(verdict({ a: { status: 'watch' }, b: { status: 'off' } }).cls, 'off');
});

test('price edges are described in words', () => {
  assert.match(describeFlank({ kind: 'change', field: 'in', tag: 'morph/fp8', old: 0.02, new: 0.022 }), /morph\/fp8: Input 0,02 → 0,022 \$\/M/);
  assert.match(describeFlank({ kind: 'vanished', tag: 'x' }), /nicht mehr verfügbar/);
  assert.match(describeFlank({ kind: 'change', field: 'ok', tag: 'x', old: true, new: false }), /nicht mehr zulässig/);
});

test('charts render SVG, escape labels and return empty strings without data', () => {
  assert.equal(lineChart([{ points: [] }]), '');
  assert.equal(pairBars([]), '');
  const svg = lineChart([{ cls: 'exp', dash: '6 5', points: [[0, 1], [10, 2], [20, null], [30, 3]] }], { w: 400, h: 200, label: 'a<b' });
  assert.match(svg, /^<svg viewBox="0 0 400 200"/);
  assert.match(svg, /aria-label="a&lt;b"/);
  assert.equal((svg.match(/<path class="line exp"/g) || []).length, 1);
  assert.ok(svg.includes('stroke-dasharray="6 5"'));
  assert.equal((svg.match(/M/g) || []).length >= 2, true); // the null gap starts a new sub-path
  const bars = pairBars([{ label: '1', a: 2, b: 3 }, { label: '2', a: null, b: 1 }], { w: 400, h: 200 });
  assert.equal((bars.match(/class="bar-exp"/g) || []).length, 1);
  assert.equal((bars.match(/class="bar-mea"/g) || []).length, 2);
  assert.match(bullets([{ label: 'morph/fp8', measured: 0.9, expected: 0.93, valueText: '90 %' }]), /class="tick"/);
});

test('explanations take thresholds from the data, never hard-coded limits', () => {
  const x = explain('switching', { rel_gain_min: 0.2, payback_max_min: 45, cache_ttl_min: 10 });
  assert.match(x.body.join(' '), /20 %/);
  assert.match(x.body.join(' '), /45 Minuten/);
  assert.match(explain('cache_ttl', { cache_ttl_min: 10 }).title, /10 Minuten/);
  assert.equal(explain('nope').body.length, 0);
});

const PROF = { hit: 0.94, out_ratio: 0.0025 };
const E = (id, prompt, completion, cache_read, extra = {}) => ({ id, short: id, free: false, tools: true, context: 1000000, pricing: { prompt, completion, cache_read }, ...extra });

test('entryUnitCost uses the cache price when present and the full input price otherwise', () => {
  const withCache = entryUnitCost(E('a', 0.15, 0.6, 0.003), PROF);
  const noCache = entryUnitCost(E('b', 0.15, 0.6, null), PROF);
  assert.ok(withCache < noCache);
  assert.ok(Math.abs(withCache - unitCost({ in: 0.15, out: 0.6, cr: 0.003 }, PROF)) < 1e-12);
  assert.equal(entryUnitCost({ pricing: null }, PROF), null);
  assert.equal(entryUnitCost(E('c', null, 1, 1), PROF), null);
});

test('pickTop3 takes the three cheapest paid, tool-capable, big-context entries', () => {
  const entries = [
    E('cheap-free', 0, 0, 0, { free: true }), E('no-tools', 0.01, 0.01, 0.001, { tools: false }), E('tiny-ctx', 0.01, 0.01, 0.001, { context: 32000 }),
    E('d', 0.5, 2, 0.05), E('a', 0.15, 0.6, 0.003), E('c', 0.3, 1, 0.02), E('b', 0.2, 0.8, 0.01), E('e', 4, 20, 0.2),
  ];
  assert.deepEqual(pickTop3(entries, PROF).map((e) => e.id), ['a', 'b', 'c']);
  assert.deepEqual(pickTop3([], PROF), []);
});

test('leaderboard and free list render escaped HTML, highlight stealth alerts and survive missing data', () => {
  const el = { innerHTML: '', hidden: false };
  renderMarket(el, null, null);
  assert.match(el.innerHTML, /noch nicht veröffentlicht/);
  const T = {
    generated_at: '2026-10-31T10:00:00Z', baseline: false, source: { day_date: '2026-10-30', week_dates: ['2026-10-24'] },
    day: [E('x/<script>alert(1)</script>', 0.15, 0.6, 0.003, { rank: 1, key: 'k1', short: '<b>evil</b>', provider: 'P', link: 'https://openrouter.ai/models/x', tokens: 8e12 })],
    week: [], diff: { day: { new: [{ key: 'k1' }] }, week: { new: [] } },
    free_models: [{ id: 'o/m', short: 'Oxalpha <i>', provider: 'O', context: 1e6, tools: true, stealth: true, new: true, alert: true, age_days: 2, link: 'https://openrouter.ai/models/o/m', expires: null }],
  };
  renderMarket(el, T, { model: 'x', overview: { profile: PROF, current: 'morph/fp8' }, rankings: { week: { rows: [{ tag: 'morph/fp8', unit: 0.0056 }] } } });
  assert.ok(!el.innerHTML.includes('<script>') && !el.innerHTML.includes('<b>evil</b>'));
  assert.match(el.innerHTML, /neu im Ranking/);
  assert.match(el.innerHTML, /8,0 Bio|8 Bio/);
  const fr = { innerHTML: '' }, alert = { innerHTML: '', hidden: true };
  renderFree(fr, alert, T);
  assert.equal(alert.hidden, false);
  assert.match(alert.innerHTML, /Neues Stealth-Modell/);
  assert.ok(!alert.innerHTML.includes('<i>'));
  assert.match(fr.innerHTML, /class="free glow"/);
  const none = { innerHTML: '', hidden: true };
  renderFree(fr, none, { free_models: [] });
  assert.equal(none.hidden, true);
  assert.match(fr.innerHTML, /keine kostenlosen/);
});

test('dayTs reads a YYYY-MM-DD exporter day as a LOCAL calendar day (not UTC)', () => {
  const d = new Date(dayTs('2026-10-30') * 1000);
  assert.equal(d.getDate(), 30);
  assert.equal(d.getMonth(), 9);
  assert.equal(d.getHours(), 12);
});

test('external links only ever point to openrouter.ai, open in a new tab and carry an accessible label', () => {
  assert.equal(safeOrUrl('https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017'), 'https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017');
  assert.equal(safeOrUrl('https://openrouter.ai/nvidia/x:free'), 'https://openrouter.ai/nvidia/x:free');
  for (const bad of ['javascript:alert(1)', 'http://openrouter.ai/x', 'https://evil.example/openrouter.ai/', 'https://openrouter.ai.evil.example/x', 'https://openrouter.ai/"onclick=', null, 42]) {
    assert.equal(safeOrUrl(bad), null, String(bad));
    assert.equal(extLink(bad, 'x', 'y'), '');
  }
  const html = extLink(orModelUrl('deepseek/deepseek-v4.1-flash-1017'), '', 'Modellseite');
  assert.match(html, /target="_blank"/);
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, /aria-label="Modellseite \(öffnet OpenRouter in neuem Tab\)"/);
  assert.match(html, /<svg/);
  assert.equal(orRankingsUrl('week'), 'https://openrouter.ai/rankings?view=week');
  assert.equal(orRankingsUrl('day'), 'https://openrouter.ai/rankings?view=day');
  assert.equal(orRankingsUrl(), 'https://openrouter.ai/rankings');
});

test('leaderboard rows and columns offer external icons to the model page and the rankings (day and week)', () => {
  const el = { innerHTML: '' };
  const T = {
    generated_at: '2026-10-31T10:00:00Z', baseline: true, source: { day_date: '2026-10-30', week_dates: ['2026-10-24'] },
    day: [E('deepseek/deepseek-v4.1-flash-1017', 0.15, 0.6, 0.003, { rank: 1, key: 'k1', short: 'Flash', provider: 'D', link: 'javascript:alert(1)', tokens: 1e9 })], week: [], diff: {}, free_models: [],
  };
  renderMarket(el, T, null);
  assert.match(el.innerHTML, /href="https:\/\/openrouter\.ai\/rankings\?view=day"/);
  assert.match(el.innerHTML, /href="https:\/\/openrouter\.ai\/rankings\?view=week"/);
  assert.match(el.innerHTML, /href="https:\/\/openrouter\.ai\/deepseek\/deepseek-v4\.1-flash-1017"/);
  assert.ok(!el.innerHTML.includes('javascript:'), 'untrusted link field must never reach href');
  const fr = { innerHTML: '' };
  renderFree(fr, { innerHTML: '', hidden: true }, { free_models: [{ id: 'a/b:free', short: 'AB', provider: 'A', link: 'https://openrouter.ai/a/b:free', context: 1e5, tools: true }, { id: 'c/d', short: 'CD', provider: 'C', link: 'https://evil.example/', context: 1e5, tools: true }] });
  assert.match(fr.innerHTML, /href="https:\/\/openrouter\.ai\/a\/b:free"/);
  assert.ok(!fr.innerHTML.includes('evil.example'));
});

test('pickTop3 skips the async-only batch variant', () => {
  const entries = [E('batchy', 0.01, 0.01, 0.001, { variant: 'batch' }), E('a', 0.15, 0.6, 0.003, { variant: 'standard' }), E('b', 0.2, 0.8, 0.01)];
  assert.deepEqual(pickTop3(entries, PROF).map((e) => e.id), ['a', 'b']);
});

test('an exporter error is shown to the viewer instead of silently serving stale numbers', () => {
  assert.equal(errorNotice(null), '');
  assert.equal(errorNotice({}), '');
  const t = errorNotice({ error: 'no eligible endpoint', error_at: 1000 }, 1000 + 3 * 3600);
  assert.match(t, /kein zulässiger Anbieter/);
  assert.match(t, /vor 3 h/);
  assert.match(errorNotice({ error: 'boom' }), /fehlgeschlagen/);
});
