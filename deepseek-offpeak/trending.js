/*! tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages */
// OpenRouter leaderboard (day | week side by side), "my top 3" price picks and the free/stealth model list.
// Data: models_trending.json from scripts/models/openrouter_trending.py (public OpenRouter rankings feed + catalog).
import { EXT_ICON, ago, entryUnitCost, extLink, fmtPct, fmtPrice, fmtTok, fmtUsd, orModelUrl, orRankingsUrl, pickTop3, safeOrUrl, tsSec } from './model.js?v=20261102d';

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const tag = (cls, text, title = '') => `<span class="tg ${cls}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
const ctxText = (c) => (c ? (c >= 1e6 ? `${Math.round(c / 1e5) / 10} M` : `${Math.round(c / 1e3)} k`) + ' Kontext' : '');

function dayText(date) {
  if (!date) return '';
  const [y, m, d] = String(date).split('-').map(Number);
  return y ? `${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')}.` : '';
}

function priceText(p) {
  if (!p || p.prompt === null || p.prompt === undefined) return '–';
  if (p.prompt === 0 && p.completion === 0) return 'kostenlos';
  return `${fmtPrice(p.prompt)} / ${fmtPrice(p.completion)}`;
}

function row(e, ours, newKeys) {
  const tags = [];
  if (e.free) tags.push(tag(e.stealth ? 'stealth' : 'free', e.stealth ? 'Stealth' : 'Gratis'));
  if (newKeys.has(e.key)) tags.push(tag('new', 'neu im Ranking', 'Vor dem letzten Abruf nicht in den Top 10'));
  if (!e.tools) tags.push(tag('dim', 'ohne Tools', 'Kein Tool-Aufruf: für Agenten ungeeignet'));
  const cache = e.pricing && e.pricing.cache_read ? `Cache ${fmtPrice(e.pricing.cache_read)}` : '';
  return `<li class="lb-row${e.id === ours ? ' ours' : ''}"><span class="rk">${e.rank}</span>
    <span class="nm"><span class="nm-top"><b>${esc(e.short || e.name)}</b>${extLink(orModelUrl(e.id), '', `${e.short || e.name}: Modellseite`, 'ext ico')}</span><small>${esc(e.provider)} · ${esc(ctxText(e.context))}</small>${tags.length ? `<span class="tgs">${tags.join('')}</span>` : ''}</span>
    <span class="px"><b>${esc(priceText(e.pricing))}</b><small>${esc(cache)}</small></span>
    <span class="tk">${fmtTok(e.tokens)}<small>Token</small></span></li>`;
}

function picks(entries, prof, ours) {
  const best = pickTop3(entries, prof);
  if (!best.length) return '<div class="empty">Keine passenden Modelle in dieser Liste.</div>';
  return `<ol class="picks">${best.map((e, i) => {
    const d = ours ? e.unit / ours - 1 : null;
    const cmp = d === null ? '' : d < 0 ? `${fmtPct(-d)} günstiger als unser Anbieter` : `${fmtPct(d)} teurer als unser Anbieter`;
    return `<li><span class="rk">${i + 1}</span><span class="nm"><span class="nm-top"><b>${esc(e.short || e.name)}</b>${extLink(orModelUrl(e.id), '', `${e.short || e.name}: Modellseite`, 'ext ico')}</span><small>${esc(cmp)}</small></span><span class="px"><b>${fmtUsd(e.unit)}</b><small>pro 1M Prompt</small></span></li>`;
  }).join('')}</ol>`;
}

function column(title, sub, entries, diff, baseline, prof, ours, oursUnit, view) {
  const newKeys = new Set(baseline ? [] : ((diff && diff.new) || []).map((n) => n.key));
  return `<div class="col"><div class="col-h"><h3>${esc(title)}</h3><span class="sub">${esc(sub)}</span>${extLink(orRankingsUrl(view), 'Rankings', `OpenRouter-Rankings (${title})`, 'ext right')}</div>
    <div class="picks-h">Meine Top 3 <span class="sub">günstigste der Liste mit Tool-Support, bei unserer Cache-Quote</span>
      <button class="i inline" data-explain="picks" aria-label="Erklärung: Meine Top 3">i</button></div>
    ${picks(entries, prof, oursUnit)}
    <ol class="lb" aria-label="${esc(title)}">${entries.map((e) => row(e, ours, newKeys)).join('')}</ol></div>`;
}

export function renderMarket(el, T, D) {
  if (!T) { el.innerHTML = '<div class="empty">Die OpenRouter-Rangliste ist noch nicht veröffentlicht.</div>'; return; }
  const prof = (D && D.overview && D.overview.profile) || { hit: 0.9, out_ratio: 0.0025 };
  const ours = D ? D.model : null;
  const wk = D && D.rankings && D.rankings.week && D.rankings.week.rows.find((r) => r.tag === D.overview.current);
  const oursUnit = wk ? wk.unit : null;
  const now = Date.now() / 1000;
  const gen = tsSec(T.generated_at);
  el.innerHTML = `<p class="lead">Beliebteste Modelle auf OpenRouter nach Token-Verbrauch, mit Preisen in $ pro 1M Token (Input / Output). Unser Modell ist markiert. ${gen === null ? '' : `Stand ${ago(gen, now)}.`}</p>
    <div class="cols">${column('Heute', `Tag ${dayText(T.source.day_date)}`, T.day, T.diff && T.diff.day, T.baseline, prof, ours, oursUnit, 'day')}
    ${column('Diese Woche', `${T.source.week_dates.length} Tage`, T.week, T.diff && T.diff.week, T.baseline, prof, ours, oursUnit, 'week')}</div>
    <div class="note">Quelle: öffentliche OpenRouter-Rankings und -Katalog. „Meine Top 3“ ist eine reine Preisrechnung (keine Qualitätsbewertung); Beliebtheit dient als Qualitätsanzeichen.</div>`;
}

export function renderFree(el, alertEl, T) {
  alertEl.hidden = true;
  if (!T) { el.innerHTML = '<div class="empty">Keine Daten.</div>'; return; }
  const list = T.free_models || [];
  const alerts = list.filter((m) => m.alert);
  if (alerts.length) {
    alertEl.hidden = false;
    alertEl.innerHTML = `<span class="spark" aria-hidden="true">✦</span><div><b>Neues Stealth-Modell${alerts.length > 1 ? 'e' : ''}:</b> ${alerts.map((m) => `${safeOrUrl(m.link) ? `<a href="${esc(m.link)}" target="_blank" rel="noopener noreferrer">${esc(m.short || m.name)}${EXT_ICON}</a>` : esc(m.short || m.name)}`).join(', ')} <small>(kostenlos, nicht als „free“ gekennzeichnet, jünger als 14 Tage)</small></div>`;
  }
  if (!list.length) { el.innerHTML = '<div class="empty">Zurzeit gibt es keine kostenlosen Modelle.</div>'; return; }
  el.innerHTML = `<div class="free-grid">${list.map((m) => {
    const tags = [];
    if (m.stealth) tags.push(tag('stealth', 'Stealth', 'Kostenlos, aber nicht als free gekennzeichnet'));
    if (m.new) tags.push(tag('new', 'neu', 'Weniger als 14 Tage im Katalog'));
    if (m.tools) tags.push(tag('dim', 'Tools')); else tags.push(tag('dim', 'ohne Tools'));
    if (m.expires) tags.push(tag('warn', `bis ${esc(String(m.expires).slice(5).split('-').reverse().join('.'))}`, 'Angebot läuft ab'));
    const href = safeOrUrl(m.link);
    return `<${href ? 'a' : 'div'} class="free${m.alert ? ' glow' : m.stealth ? ' stealth-box' : ''}"${href ? ` href="${esc(href)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(m.short || m.name)} (öffnet OpenRouter in neuem Tab)"` : ''}><b>${esc(m.short || m.name)}${href ? EXT_ICON : ''}</b><small>${esc(m.provider)} · ${esc(ctxText(m.context))}${m.age_days !== null && m.age_days !== undefined ? ` · ${Math.round(m.age_days)} Tage alt` : ''}</small><span class="tgs">${tags.join('')}</span></${href ? 'a' : 'div'}>`;
  }).join('')}</div>`;
}
