/*! tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages */
// Provider watchlist: every provider that was rank 1 (cheapest for our usage) at some point in the last 7 days, its price curve,
// the phases in which it was clearly cheap, and the hours in which it is typically cheap. Read-only, no controls on the router.
import { ago, dayLabel, fmtDur, fmtPct, fmtPrice, hhmm } from './model.js?v=20261102d';
import { lineChart } from './charts.js?v=20261102d';

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const swatch = (i) => `s${(i % 8) + 1}`;
/** "So 02.11." for the x axis (dayLabel alone is "So, 02.11." and was cut to "So, 0"). */
const dayTick = (t) => { const s = dayLabel(t); return s.slice(0, 2) + ' ' + s.slice(4); };

function phaseText([a, b], now) {
  const dur = Math.max(60, (b || now) - a);
  const day = dayLabel(a) === dayLabel(now) ? '' : dayLabel(a) + ' ';
  return `${day}${hhmm(a)}–${hhmm(b || now)} · ${fmtDur(dur)}`;
}

/** One row per watched provider plus the multi-line chart. S: {whide:Set, wfocus:tag|null} - legend clicks toggle, row clicks focus. */
export function renderWatch(el, D, S, openDetail) {
  const W = D && D.watch;
  const now = Date.now() / 1000;
  if (!W || !W.tags || !W.tags.length) {
    el.innerHTML = `<div class="empty">Noch kein Preisverlauf aufgezeichnet. Die Überwachung füllt sich, sobald der Router Preise abruft.</div>`;
    return;
  }
  S.whide = S.whide instanceof Set ? S.whide : new Set();
  const hide = S.whide;
  const focus = S.wfocus || null;
  const tags = W.tags;
  const cls = new Map(tags.map((t, i) => [t.tag, swatch(i)]));
  const shownTags = tags.filter((t) => (focus ? t.tag === focus : !hide.has(t.tag)));

  const legend = tags.map((t) => `<button class="lg${hide.has(t.tag) ? ' off' : ''}${focus === t.tag ? ' focus' : ''}" data-lg="${esc(t.tag)}"
      aria-pressed="${hide.has(t.tag) ? 'false' : 'true'}" title="${hide.has(t.tag) ? 'einblenden' : 'ausblenden'}: ${esc(t.tag)}">
      <i class="sw ${cls.get(t.tag)}" aria-hidden="true"></i><span>${esc(t.tag)}</span><b>${fmtPrice(t.now)}</b>${t.rank ? `<span class="rk">#${t.rank}</span>` : ''}</button>`).join('');

  let chart = '';
  const pts = shownTags.flatMap((t) => (W.series[t.tag] || []));
  if (pts.length) {
    const x0 = Math.min(...pts.map((p) => p[0]));
    const x1 = Math.max(now, ...pts.map((p) => p[0]));
    const vals = pts.map((p) => p[1]).filter((v) => v !== null && v !== undefined);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) || hi * 0.08 || 0.001;
    const ticks = [];
    const d0 = new Date(x0 * 1000);
    d0.setHours(0, 0, 0, 0);
    for (let t = d0.getTime() / 1000; t <= x1; t += 86400) if (t > x0) ticks.push(t);
    chart = lineChart(shownTags.map((t) => ({ cls: cls.get(t.tag), points: W.series[t.tag] || [] })),
      { w: Math.max(280, Math.floor(el.clientWidth || 640)) - 8, h: 230, xMin: x0, xMax: x1, xTicks: ticks, xFmt: dayTick,
        yMin: Math.max(0, lo - pad * 0.3), yMax: hi + pad * 0.3,
        yFmt: (v) => '$' + fmtPrice(v), label: 'Preis je 1M Prompt-Token der beobachteten Anbieter, letzte 7 Tage' });
  } else {
    chart = `<div class="empty">Keine Linie ausgewählt. Klick auf die Legende blendet einen Anbieter wieder ein.</div>`;
  }

  const rows = tags.map((t) => {
    const rank1 = t.rank === 1;
    const cheapH = t.cheap.reduce((s, [a, b]) => s + Math.max(0, (b || now) - a), 0);
    return `<tr data-watch="${esc(t.tag)}" class="${rank1 ? 'win' : ''}${focus === t.tag ? ' cur' : ''}">
      <td><i class="sw ${cls.get(t.tag)}" aria-hidden="true"></i> ${esc(t.tag)}<span class="q">${Number(t.samples)} Messpunkte · Median ${fmtPrice(t.median)}</span></td>
      <td data-l="jetzt">${fmtPrice(t.now)}</td>
      <td data-l="Platz">${t.rank ? '#' + t.rank : '–'}</td>
      <td data-l="Platz 1">${rank1 ? 'seit ' + fmtDur(now - (t.since || now)) : 'zuletzt ' + ago(t.last_rank1, now)}</td>
      <td data-l="billige Phasen">${t.cheap.length ? `${t.cheap.length}× · ${fmtDur(cheapH)}` : '–'}</td>
      <td data-l="günstigste Stunden">${t.best_hours.length ? t.best_hours.map((h) => String(h).padStart(2, '0')).join(', ') + ' Uhr' : (t.hours_days >= 3 ? '–' : 'zu wenig Verlauf')}</td>
    </tr>`;
  }).join('');

  const hidden = [...hide].length;
  el.innerHTML = `<div class="lg-wrap"><div class="legend">${legend}</div>${focus || hidden
    ? `<button class="btn slim" data-wreset>${focus ? 'Fokus aufheben' : 'alle wieder zeigen'}</button>` : ''}</div>
    ${chart}
    <div class="note">Preis je 1M Prompt bei unserem Verbrauch (Cache-Quote ${fmtPct((D.overview.profile || {}).hit, 1)}, Output ${fmtPct((D.overview.profile || {}).out_ratio, 2)}),
      Y-Achse auf den Preisbereich gezoomt. Legende anklicken = aus-/einblenden, Zeile anklicken = hervorheben und auswerten.</div>
    <table class="rank watch"><thead><tr><th>Anbieter</th><th>jetzt $/1M</th><th>Platz</th><th>Platz 1</th><th>billige Phasen</th><th>günstigste Stunden</th></tr></thead><tbody>${rows}</tbody></table>`;

  el.querySelectorAll('[data-lg]').forEach((b) => b.addEventListener('click', () => {
    const tag = b.dataset.lg;
    if (S.wfocus === tag) S.wfocus = null;
    else if (hide.has(tag)) hide.delete(tag);
    else hide.add(tag);
    renderWatch(el, D, S, openDetail);
  }));
  el.querySelectorAll('[data-watch]').forEach((tr) => tr.addEventListener('click', () => {
    const tag = tr.dataset.watch;
    S.wfocus = S.wfocus === tag ? null : tag;
    renderWatch(el, D, S, openDetail);
    if (openDetail) openDetail(tag);
  }));
  const reset = el.querySelector('[data-wreset]');
  if (reset) reset.addEventListener('click', () => { S.wfocus = null; S.whide = new Set(); renderWatch(el, D, S, openDetail); });
}

/** Dialog body for one provider: rank-1 share, cheap phases, typical hours. */
export function watchDetail(D, tag) {
  const W = D.watch || {};
  const now = Date.now() / 1000;
  const t = (W.tags || []).find((x) => x.tag === tag);
  if (!t) return { title: tag, html: '<p>Für diesen Anbieter liegt kein Verlauf vor.</p>' };
  const win = (W.days || 7) * 86400;
  const rank1s = t.rank1.reduce((s, [a, b]) => s + Math.max(0, (b || now) - a), 0);
  const cheap = t.cheap.slice(-6).reverse().map(([a, b, low]) =>
    `<li>${phaseText([a, b], now)}${t.median && low ? ` · <b>${fmtPct(1 - low / t.median, 0)}</b> unter dem eigenen Median` : ''}</li>`).join('');
  return {
    title: `Anbieter ${tag}`,
    html: `<p><b>Aktuell:</b> ${fmtPrice(t.now)} $ je 1M Prompt${t.rank ? ` · Platz ${t.rank}` : ''} · Median der Woche ${fmtPrice(t.median)} · billigster Wert ${fmtPrice(t.min)} (${ago(t.min_at, now)})</p>
      <p><b>Platz 1:</b> ${fmtPct(rank1s / win, 1)} der letzten ${W.days} Tage (${fmtDur(rank1s)})${t.rank === 1 ? ' · läuft gerade' : ` · zuletzt ${ago(t.last_rank1, now)}`}</p>
      <p><b>Typisch günstige Stunden:</b> ${t.best_hours.length ? t.best_hours.map((h) => String(h).padStart(2, '0') + ' Uhr').join(', ') : (t.hours_days >= 3 ? 'kein Muster erkennbar' : `noch zu wenig Verlauf (${t.hours_days} Tage)`)}</p>
      ${cheap ? `<p><b>Billige Phasen (≤ ${fmtPct(1 - (W.peak_rel || 0.8), 0)} unter Median):</b></p><ul class="phases">${cheap}</ul>` : '<p>Noch keine klar billige Phase aufgezeichnet.</p>'}
      <p class="note">Ein Anbieter bleibt auf der Liste, solange er innerhalb von ${W.days} Tagen mindestens einmal Platz 1 war.</p>`,
  };
}
