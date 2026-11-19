// Pure helpers for the router dashboard (no DOM): formatting, cost model, badge semantics, what-if ranking.
// The cost formula mirrors scripts/models/or_provider_router.py unit_cost(): USD per 1M prompt tokens at a given cache-hit rate.

const de = (min, max) => new Intl.NumberFormat('de-DE', { minimumFractionDigits: min, maximumFractionDigits: max });
const f0 = de(0, 0), f1 = de(1, 1), f2 = de(2, 2);

export function fmtUsd(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return '–';
  const a = Math.abs(x);
  if (a === 0) return '$0';
  if (a < 0.01) return '$' + de(4, 4).format(x);
  if (a < 10) return '$' + f2.format(x);
  return '$' + f1.format(x);
}

/** Billed amounts: always 2 decimals ($31,73), unlike fmtUsd which switches to 1 from $10. */
export function fmtUsd2(x) {
  if (x === null || x === undefined || Number.isNaN(x)) return '–';
  if (Math.abs(x) < 0.01) return '$' + de(4, 4).format(x);
  return '$' + f2.format(x);
}

export function fmtTok(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '–';
  const a = Math.abs(n);
  if (a >= 1e12) return f1.format(n / 1e12) + ' Bio';
  if (a >= 1e9) return f2.format(n / 1e9) + ' Mrd';
  if (a >= 1e6) return f1.format(n / 1e6) + ' Mio';
  if (a >= 1e3) return f0.format(n / 1e3) + ' k';
  return f0.format(n);
}

export function fmtPct(x, digits = 0) {
  if (x === null || x === undefined || Number.isNaN(x)) return '–';
  return de(digits, digits).format(x * 100) + ' %';
}

/** Duration in German: 45 min, 3 h 20 min, 2 d 4 h (phases on the provider watchlist). */
export function fmtDur(sec) {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return '–';
  const s = Math.max(0, Math.round(sec));
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${String(Math.round((s % 3600) / 60)).padStart(2, '0')} min`;
  return `${Math.floor(s / 86400)} d ${Math.round((s % 86400) / 3600)} h`;
}

export function fmtPrice(x) {
  if (x === null || x === undefined) return '–';
  return de(2, 4).format(x);
}

export function ago(ts, now = Date.now() / 1000) {
  if (!ts) return 'nie';
  const s = Math.max(0, now - ts);
  if (s < 90) return 'gerade eben';
  if (s < 5400) return `vor ${Math.round(s / 60)} min`;
  if (s < 129600) return `vor ${Math.round(s / 3600)} h`;
  return `vor ${Math.round(s / 86400)} d`;
}

export function hhmm(ts) {
  return new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(new Date(ts * 1000));
}

/** 'YYYY-MM-DD' (a local calendar day from the exporter) -> epoch seconds at local noon; Date.parse would read it as UTC. */
export function dayTs(day) {
  const [y, m, d] = String(day).split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1, 12).getTime() / 1000;
}

export function dayHour(ts) {
  return new Intl.DateTimeFormat('de-DE', { weekday: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(ts * 1000));
}

export function dayLabel(ts) {
  return new Intl.DateTimeFormat('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit' }).format(new Date(ts * 1000));
}

/** Epoch seconds from a number or an ISO string. router.json uses epoch seconds, models_trending.json an ISO string -
 *  ago() with the raw string produced "vor NaN d" (2026-11-02). */
export function tsSec(x) {
  if (typeof x === 'number' && Number.isFinite(x)) return x;
  const t = Date.parse(String(x ?? ''));
  return Number.isNaN(t) ? null : t / 1000;
}

export function unitCost(ep, prof) {
  return (1 - prof.hit) * ep.in + prof.hit * ep.cr + prof.out_ratio * ep.out;
}

/** What-if ranking from the provider price table: tokens/day, cache hit, output ratio -> rows sorted by cost with delta to the winner. */
export function whatIf(providers, { tokensPerDay, hit, outRatio }) {
  const rows = providers.map((p) => {
    const unit = unitCost(p, { hit, out_ratio: outRatio });
    return { tag: p.tag, quant: p.quant, unit, usd: (tokensPerDay * unit) / 1e6 };
  }).sort((a, b) => a.unit - b.unit);
  const top = rows[0] ? rows[0].unit : 0;
  return rows.map((r) => ({ ...r, delta: top ? r.unit / top - 1 : 0 }));
}

/** Cache-hit rate at which provider a and b cost the same (null if never / identical). */
export function breakevenHit(a, b, outRatio = 0) {
  const da = a.in - a.cr, db = b.in - b.cr;
  const denom = db - da;
  if (denom === 0) return null;
  const h = (b.in - a.in + outRatio * (b.out - a.out)) / denom;
  return h > 0 && h < 1 ? h : null;
}

export const STATUS = {
  ok: { label: 'ok', icon: '✓', cls: 'ok', title: 'Erwartung und Messung stimmen überein' },
  watch: { label: 'beobachten', icon: '▲', cls: 'watch', title: 'Leichte Abweichung' },
  off: { label: 'abweichend', icon: '◆', cls: 'off', title: 'Deutliche Abweichung' },
  na: { label: 'sammelt Daten', icon: '…', cls: 'na', title: 'Noch zu wenig Messwerte' },
};

export function statusOf(badge) {
  return STATUS[(badge && badge.status) || 'na'] || STATUS.na;
}

/** Short German verdict for the hero from the four control badges. */
export function verdict(badges) {
  const st = Object.values(badges || {}).map((b) => b.status);
  if (!st.length || st.every((s) => s === 'na')) return { cls: 'na', text: 'Noch keine Messwerte – der Router sammelt Daten.' };
  if (st.includes('off')) return { cls: 'off', text: 'Mindestens eine Messung weicht deutlich von der Erwartung ab.' };
  if (st.includes('watch')) return { cls: 'watch', text: 'Erwartung und Messung weichen leicht ab.' };
  return { cls: 'ok', text: 'Erwartung und Messung stimmen überein.' };
}

export function describeFlank(e) {
  if (e.kind === 'appeared') return `${e.tag} neu im Angebot`;
  if (e.kind === 'vanished') return `${e.tag} nicht mehr verfügbar`;
  const names = { in: 'Input', out: 'Output', cr: 'Cache-Read', ok: 'Verfügbarkeit' };
  const what = names[e.field] || e.field;
  if (e.field === 'ok') return `${e.tag}: ${e.new ? 'wieder zulässig' : 'nicht mehr zulässig'}`;
  return `${e.tag}: ${what} ${fmtPrice(e.old)} → ${fmtPrice(e.new)} $/M`;
}

export function clamp(x, lo, hi) {
  return Math.min(hi, Math.max(lo, x));
}

/** USD per 1M prompt tokens of a leaderboard/catalog entry (prices per 1M) at our cache profile; missing cache price = no caching discount. */
export function entryUnitCost(e, prof) {
  const p = e && e.pricing;
  if (!p || p.prompt === null || p.prompt === undefined || p.completion === null || p.completion === undefined) return null;
  const cr = p.cache_read === null || p.cache_read === undefined ? p.prompt : p.cache_read;
  return unitCost({ in: p.prompt, out: p.completion, cr }, prof);
}

/** The three cheapest paid entries that can run agents (tool calls) with a big enough context. Price only, no quality judgement. */
export function pickTop3(entries, prof, { minContext = 128000 } = {}) {
  return (entries || [])
    .filter((e) => !e.free && e.variant !== 'batch' && e.tools && (e.context || 0) >= minContext)
    .map((e) => ({ ...e, unit: entryUnitCost(e, prof) }))
    .filter((e) => e.unit !== null)
    .sort((a, b) => a.unit - b.unit)
    .slice(0, 3);
}

const escHtml = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const EXT_ICON = '<svg class="ext-ic" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M9 2h5v5M14 2 7.5 8.5M12 9.5V13a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

/** Only plain https://openrouter.ai/... links are ever rendered (data from the feed is never trusted as a URL). */
export function safeOrUrl(u) {
  return typeof u === 'string' && /^https:\/\/openrouter\.ai\/[\w./:%?=&~+-]*$/.test(u) ? u : null;
}

export const orModelUrl = (id) => safeOrUrl(`https://openrouter.ai/${id}`);
export const orRankingsUrl = (view) => `https://openrouter.ai/rankings${view ? `?view=${view === 'week' ? 'week' : 'day'}` : ''}`;

/** External link to OpenRouter in a new tab; icon-only when text is empty. Returns '' for any URL that is not on openrouter.ai. */
export function extLink(url, text, label, cls = 'ext') {
  const u = safeOrUrl(url);
  if (!u) return '';
  return `<a class="${cls}" href="${escHtml(u)}" target="_blank" rel="noopener noreferrer" aria-label="${escHtml(label)} (öffnet OpenRouter in neuem Tab)" title="${escHtml(label)}">${text ? escHtml(text) : ''}${EXT_ICON}</a>`;
}

/** Warning text when the exporter had no usable data (router.json carries `error`): the numbers shown are then the last valid state. */
export function errorNotice(D, now = Date.now() / 1000) {
  if (!D || !D.error) return '';
  const when = D.error_at ? ago(D.error_at, now) : 'zuletzt';
  return `Achtung: ${D.error === 'no eligible endpoint' ? 'zurzeit ist kein zulässiger Anbieter bekannt' : 'die letzte Berechnung ist fehlgeschlagen'} (${when}). Die Zahlen stammen vom letzten gültigen Stand.`;
}
