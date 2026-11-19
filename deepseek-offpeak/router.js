// Router dashboard: read-only visualisation of router.json (no controls, no secrets). Renders into the sections of index.html.
import { ago, breakevenHit, clamp, dayHour, dayTs, errorNotice, orModelUrl, dayLabel, describeFlank, fmtPct, fmtPrice, fmtTok, fmtUsd, fmtUsd2, hhmm, statusOf, verdict, whatIf } from './model.js?v=20261102d';
import { bullets, lineChart, pairBars } from './charts.js?v=20261102d';
import { explain } from './explain.js?v=20261102d';
import { renderWatch, watchDetail } from './watch.js?v=20261102d';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const info = (key, label) => `<button class="i inline" data-explain="${key}" aria-label="Erklärung: ${esc(label)}">i</button>`;
const de1 = (x) => new Intl.NumberFormat('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(x);
const STALE_S = 30 * 60;

let D = null;
const S = { rank: 'today', fc: 'usd', log: 'all', wi: null, whide: null, wfocus: null };

async function load() {
  try {
    const r = await fetch(`./router.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    const doc = await r.json();
    if (!doc.freshness) throw new Error('incomplete document'); // fallback document of a failed first export
    D = doc;
    render();
  } catch (e) {
    $('fresh').textContent = 'Daten konnten nicht geladen werden.';
    if (!D) $('heroBody').innerHTML = '<div class="empty">Noch keine Router-Daten veröffentlicht.</div>';
  }
}

const width = (el) => Math.max(280, Math.floor(el.clientWidth || 320));
const emptyBox = (txt) => `<div class="empty">${txt}</div>`;
const since = () => (D.freshness.first_decision ? `seit ${dayLabel(D.freshness.first_decision)} ${hhmm(D.freshness.first_decision)}` : '');

function tabs(el, items, current, onPick) {
  el.setAttribute('role', 'tablist');
  el.innerHTML = items.map(([k, label]) => `<button class="tab" role="tab" data-k="${k}" aria-selected="${k === current}" tabindex="${k === current ? 0 : -1}">${esc(label)}</button>`).join('');
  const pick = (k) => { onPick(k); };
  el.querySelectorAll('.tab').forEach((b, i, all) => {
    b.addEventListener('click', () => pick(b.dataset.k));
    b.addEventListener('keydown', (e) => {
      const j = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : null;
      if (j !== null && all[(j + all.length) % all.length]) { e.preventDefault(); pick(all[(j + all.length) % all.length].dataset.k); }
    });
  });
}

function render() {
  renderFresh(); renderHero(); renderRanking(); renderWhatIf(); renderWatchSection(); renderControl(); renderForecast(); renderLog(); renderHow(); renderFoot();
}

function openDialog(title, html) {
  $('dlgTitle').textContent = title;
  $('dlgBody').innerHTML = html;
  $('dlg').showModal();
}

function renderWatchSection() {
  renderWatch($('watchBody'), D, S, (tag) => { const x = watchDetail(D, tag); openDialog(x.title, x.html); });
}

function renderFresh() {
  const now = Date.now() / 1000;
  const age = now - D.generated_at;
  const stale = age > STALE_S;
  $('fresh').innerHTML = `Stand <b>${ago(D.generated_at, now)}</b> · letzte Nutzung <b>${ago(D.freshness.last_message, now)}</b>` +
    (stale ? ' · <b>Der Router aktualisiert nur, solange opencode benutzt wird. Die Werte sind nicht live.</b>' : '') +
    (D.error ? ` · <b class="warn-text">${esc(errorNotice(D, now))}</b>` : '');
}

/** Real OpenRouter billing next to the router arithmetic (2026-11-01/02): dashed strip, rolling 24 h from the key counter
 *  (usage_daily resets at UTC midnight, so it is NOT a 24 h window), delta against our own expectation for the same 24 h. */
function spendStrip() {
  const s = D.spend;
  const iBtn = info('spend', 'OpenRouter-Abrechnung');
  if (!s || s.usage === undefined) return `<div class="spend"><span class="lbl">OpenRouter-Abrechnung: noch kein Messwert.</span>${iBtn}</div>`;
  const now = Date.now() / 1000;
  const stale = !s.at || now - s.at > STALE_S;
  const real = s.real || {}, calib = s.calibration || {};
  const d24 = real.d24;
  // The key bills every model and every program that uses it, while the router only models the traffic it could attribute to a
  // provider. So the honest number is a factor ("key = 3.7x our arithmetic") plus the attribution share, not a scary percentage.
  let delta = '';
  if (calib.usd !== null && calib.usd !== undefined && calib.factor) {
    const f = calib.factor;
    const cls = f > 4 ? 'off' : f > 1.25 ? 'watch' : 'ok';
    delta = `<span class="pill ${cls}" title="Abrechnung des Schlüssels gegen die Router-Rechnung für dieselben 24 h">${de1(f)}× der Router-Rechnung</span>`;
  }
  const share = (D.overview.attribution || {}).share;
  const scope = calib.expected
    ? `<span class="note" style="margin-top:0">Router-Rechnung für dieselben 24 h: ${fmtUsd2(calib.expected)}` +
      (share === undefined ? '' : ` · ${fmtPct(share, 0)} der Token einem Anbieter zugeordnet`) + `</span>`
    : '';
  const reset = { daily: 'Tagesbudget', weekly: 'Wochenbudget', monthly: 'Monatsbudget' }[s.limit_reset] || 'Restbudget';
  const rest = s.limit_remaining;
  const restChip = rest === undefined || rest === null ? '' :
    `<span class="chip best${rest < 0 ? ' neg' : ''}">${reset} ${fmtUsd2(rest)} übrig</span>`;
  const approx24 = real.approx && real.approx.includes('d24');
  return `<div class="spend${stale ? ' stale' : ''}"><span class="tg">OpenRouter abrechnet</span>` +
    `<span class="grp"${approx24 ? ' title="Anfangswert aus der letzten Messung vor einer längeren Pause - der 24-h-Wert ist deshalb ungefähr"' : ''}><b class="v">${d24 === null || d24 === undefined ? '–' : fmtUsd2(d24)}</b><span class="lbl">24 h${approx24 ? ' ≈' : ''}</span></span>` +
    (s.usage_monthly !== undefined && s.usage_monthly !== null ? `<span class="grp"><b class="v">${fmtUsd2(s.usage_monthly)}</b><span class="lbl">Monat</span></span>` : '') +
    `<span class="grp"><b class="v">${fmtUsd2(s.usage)}</b><span class="lbl">gesamt</span></span>` + delta + restChip +
    (stale ? `<span class="note" style="margin-top:0">Stand ${ago(s.at, now)}</span>` : '') + iBtn + scope + `</div>`;
}

/** Platz 1 gegen Platz 2 right under the current choice: the owner asked what the second-best option costs and how far away it is. */
function compareStrip() {
  const rows = (D.rankings.today || {}).rows || [];
  const cur = D.overview.current;
  if (rows.length < 2) return '';
  const best = rows[0];
  const alt = rows[1];
  const tol = D.thresholds.rel_gain_min;
  const price = (r, i) => `<span class="cm"><span class="rk">${i}</span><b>${esc(r.tag)}</b><span class="v">${fmtUsd(r.usd)}</span>` +
    (r.tag === cur ? ' <span class="pill ok">aktiv</span>' : '') + `</span>`;
  const diff = alt.usd - best.usd;
  const close = alt.delta < tol;  // within the switch tolerance: the router may move here soon
  const why = close
    ? `Platz 2 liegt nur ${fmtPct(alt.delta)} über Platz 1 und damit innerhalb der Toleranz von ${fmtPct(tol)} – ein Wechsel ist möglich, sobald der Vorsprung die Schwelle überschreitet.`
    : `Platz 2 kostet ${fmtPct(alt.delta)} mehr als Platz 1; gewechselt wird erst ab ${fmtPct(tol)} Vorsprung.`;
  return `<div class="cmp"><span class="kicker">Platz 1 und 2 heute</span>${info('cmp', 'Platz 1 gegen Platz 2')}<div class="row">` +
    price(best, 1) + price(alt, 2) +
    `<span class="pill ${close ? 'watch' : 'ok'}">+${fmtPct(alt.delta)} · +${fmtUsd(diff)}/Tag</span></div>` +
    `<span class="note" style="margin-top:0">${why} Laufende Sitzungen zahlen bei einem Wechsel zusätzlich den warmen Cache.</span></div>`;
}

function renderHero() {
  const o = D.overview, b = D.control.badges;
  const v = verdict(b);
  const order = o.order || [];
  const chips = order.map((t, i) => `<span class="chip${t === o.best ? ' best' : ''}"><b>${i + 1}</b> ${esc(t)}${t === o.best ? ' (günstigster jetzt)' : ''}</span>`).join('');
  const bd = [['cache', 'Cache-Quote'], ['tokens', 'Token pro Tag'], ['cost', 'Kosten'], ['regret', 'Rückblick']].map(([k, label]) => {
    const st = statusOf(b[k]);
    const dev = b[k] && b[k].dev !== null && b[k].dev !== undefined ? (k === 'cache' ? `${b[k].dev.toFixed(1).replace('.', ',')} Pp` : fmtPct(b[k].dev)) : '';
    return `<a class="badge st-${st.cls}" href="#control"><span class="sym" aria-hidden="true">${st.icon}</span><span><b>${label}</b><small>${st.label}${dev ? ' · ' + dev : ''}</small></span></a>`;
  }).join('');
  const p = o.profile || {};
  const stateText = o.current === o.best ? 'bleibt' : 'wechselt bei Bedarf';
  const mea = b.cost && b.cost.samples ? b.cost.measured : null;
  $('heroBody').innerHTML = `<div class="hero-grid"><div>
    <div class="kicker">Standardwahl für neue und pausierte Sitzungen</div>
    <div class="now-row"><div class="prov">${esc(o.current || '–')}</div><span class="state"><span aria-hidden="true">●</span> ${stateText}</span></div>
    <div class="sub">${o.since ? `seit ${dayLabel(o.since)} ${hhmm(o.since)} · ` : ''}${esc(o.reason || '')}</div>
    ${compareStrip()}
    <div class="nums">
      <div class="num"><div class="v">${fmtUsd(o.today.expected_usd)}</div><div class="l">heute erwartet</div></div>
      <div class="num"><div class="v">${mea === null ? '–' : fmtUsd(mea)}</div><div class="l">intern gemessen (24 h)</div></div>
      <div class="num"><div class="v">${fmtTok(o.today.prompt)}</div><div class="l">Token heute</div></div>
    </div>
    ${spendStrip()}
    <div class="profile">Profil (${esc(p.window || '–')}): Cache-Quote ${fmtPct(p.hit, 1)} · Output ${fmtPct(p.out_ratio, 2)} · Kontext ${fmtTok(p.prompt_per_req)}/Anfrage ${info('profile', 'Nutzungsprofil')}</div>
  </div><div>
    <div class="kicker">Reihenfolge der Anbieter ${info('switching', 'Entscheidung')}</div>
    <div class="chips">${chips || '<span class="empty">keine Entscheidung</span>'}</div>
    <div class="kicker" style="margin-top:14px">Vertrauen in die Erwartung</div>
    <div class="badges">${bd}</div>
    <div class="verdict">${esc(v.text)}</div>
  </div></div>`;
}

function controlCard(title, key, badge, body) {
  const st = statusOf(badge);
  return `<article class="cc"><div class="cc-head"><h3>${title} ${info(key, title)}</h3><span class="pill ${st.cls}" title="${esc(st.title)}"><span aria-hidden="true">${st.icon}</span> ${st.label}</span></div>${body}</article>`;
}

function renderControl() {
  const c = D.control, b = c.badges, th = D.thresholds, n = since();
  const noData = (what) => emptyBox(`Noch keine Messwerte${what ? ' für ' + what : ''}. Der Router zeichnet ${n} auf, Messwerte entstehen, sobald opencode über OpenRouter benutzt wird.`);
  const W = width($('controlGrid')) > 760 ? Math.floor(width($('controlGrid')) / 2) - 24 : width($('controlGrid')) - 24;
  const H = W < 400 ? 180 : 210;

  // cache
  const hh = c.hit_hourly;
  let cache = `<div class="cc-big"><div><b>${b.cache.expected !== undefined ? fmtPct(b.cache.expected, 1) : '–'}</b>erwartet</div><div><b>${fmtPct(b.cache.measured, 1)}</b>gemessen (24 h)</div>${b.cache.dev !== null ? `<div><b>${b.cache.dev.toFixed(1).replace('.', ',')} Pp</b>Abweichung</div>` : ''}</div>`;
  if (hh.length) {
    const ts = hh.map((x) => x.h);
    const x0 = Math.min(...ts), x1 = Math.max(...ts) + 3600;
    const ticks = [0, 1, 2, 3, 4].map((i) => x0 + ((x1 - x0) * i) / 4);
    cache += lineChart([{ name: 'Erwartung', cls: 'exp', dash: '6 5', points: hh.map((x) => [x.h + 1800, x.expected]) },
      { name: 'Messung', cls: 'mea', dots: true, points: hh.map((x) => [x.h + 1800, x.measured]) }],
    { w: W, h: H, yMax: 1, yFmt: (v) => fmtPct(v), xMin: x0, xMax: x1, xTicks: ticks, xFmt: (t) => hhmm(t), label: 'Cache-Quote je Stunde, Erwartung gestrichelt, Messung durchgezogen' });
  } else cache += noData('die Cache-Quote');
  if (c.hit_by_provider.length) {
    cache += `<div class="note" style="margin-top:8px">Je Anbieter: Balken = gemessen, Strich = erwartet</div>` + bullets(c.hit_by_provider.map((p) => ({ label: p.tag, measured: p.measured, expected: p.expected, valueText: p.measured === null ? '' : fmtPct(p.measured) })), { w: W });
  }

  // tokens per day
  const td = c.tokens_daily.filter((x) => x.actual || x.forecast);
  let tok;
  if (td.some((x) => x.forecast)) {
    const done = [...td].reverse().find((x) => !x.partial && x.forecast && x.actual);
    tok = `<div class="cc-big">${done ? `<div><b>${fmtTok(done.forecast)}</b>erwartet (${dayLabel(dayTs(done.day))})</div><div><b>${fmtTok(done.actual)}</b>gemessen</div>` : ''}${b.tokens.dev !== null ? `<div><b>${fmtPct(b.tokens.dev)}</b>Abweichung</div>` : ''}</div>` +
      pairBars(td.map((x) => ({ label: dayLabel(dayTs(x.day)).slice(0, 5) + (x.partial ? '*' : ''), a: x.forecast, b: x.actual })), { w: W, h: H, yFmt: fmtTok, label: 'Token pro Tag, Prognose als Umriss, Messung gefüllt' }) +
      '<div class="note">* heutiger Tag, noch nicht abgeschlossen (zählt nicht zur Bewertung)</div>';
  } else tok = noData('die Tagesprognose') + (td.length ? `<div class="note">Gemessen wird ab heute, die Prognose wird am Morgen jedes Tages festgehalten.</div>` : '');

  // cost
  const ch = c.cost_hourly;
  let cost = `<div class="cc-big"><div><b>${fmtUsd(b.cost.expected)}</b>erwartet (24 h)</div><div><b>${fmtUsd(b.cost.measured)}</b>gemessen (Schlüssel)</div>${b.cost.dev !== null ? `<div><b>${fmtPct(b.cost.dev)}</b>Abweichung</div>` : ''}</div>`;
  cost += ch.length ? pairBars(ch.slice(-24).map((x) => ({ label: hhmm(x.h), a: x.expected, b: x.measured })), { w: W, h: H, yFmt: fmtUsd, label: 'Kosten je Stunde, erwartet als Umriss, gemessen gefüllt' }) + `<div class="note">${Number(b.cost.samples)} Messintervalle in 24 h</div>` : noData('die Kosten');

  // regret
  const rg = c.regret_daily;
  let reg;
  if (rg.length) {
    const tot = rg.reduce((s, x) => s + x.regret, 0);
    reg = `<div class="cc-big"><div><b>${fmtUsd(tot)}</b>Regret gesamt</div><div><b>${b.regret.dev !== null ? fmtPct(b.regret.dev, 1) : '–'}</b>relativ zum Besten</div></div>` +
      pairBars(rg.map((x) => ({ label: dayLabel(dayTs(x.day)).slice(0, 5), a: x.best, b: x.chosen })), { w: W, h: H, yFmt: fmtUsd, label: 'Kosten des gewählten Anbieters gegen den besten im Rückblick' }) +
      '<div class="note">Umriss = günstigster Anbieter im Rückblick, gefüllt = gewählter Anbieter</div>';
  } else reg = noData('den Rückblick');

  $('controlGrid').innerHTML = controlCard('Cache-Quote', 'cache', b.cache, cache) + controlCard('Token pro Tag', 'tokens', b.tokens, tok) +
    controlCard('Kosten', 'cost', b.cost, cost) + controlCard('Rückblick (Regret)', 'regret', b.regret, reg);
}

function renderRanking() {
  const R = D.rankings;
  const items = [['today', 'Heute'], ['week', 'Woche'], ['default', 'Standardwerte'], ['sessions', 'Sitzungen']];
  tabs($('rankTabs'), items, S.rank, (k) => { S.rank = k; renderRanking(); });
  const el = $('rankBody');
  if (S.rank === 'sessions') {
    el.innerHTML = R.sessions.length ? `<div class="sess">${R.sessions.map((s) => `<div class="sess-row"><div><b>Sitzung ${s.n}</b> · ${Number(s.msgs)} Anfragen</div><div>bester: <b>${esc(s.best)}</b></div><div class="m">${dayLabel(s.start)} ${hhmm(s.start)}–${hhmm(s.end)} · ${fmtTok(s.prompt)} Prompt · Cache ${fmtPct(s.hit)} · bester ${fmtUsd(s.usd_best)} · aktuell ${s.usd_current === null ? '–' : fmtUsd(s.usd_current)}</div></div>`).join('')}</div>` : emptyBox('Keine Sitzungen mit genug Anfragen in der letzten Woche.');
    return;
  }
  const r = R[S.rank];
  const cur = D.overview.current;
  const lbl = { today: 'heutigen Verbrauch', week: 'Verbrauch der Woche', default: 'angenommenen Standardverbrauch' }[S.rank];
  let html = '';
  if (r.empty) html += `<div class="thin">Heute gibt es noch keine Anfragen. Die Tabelle zeigt Standardwerte.</div>`;
  else if (r.thin) html += `<div class="thin">Zu wenig Daten (${Number(r.msgs)} Anfragen). Dem Ranking in diesem Zeitraum nicht trauen.</div>`;
  html += `<p class="win-line">Rechnung für ${lbl}: ${fmtTok(r.profile.prompt)} Prompt, Cache-Quote ${fmtPct(r.profile.hit, 1)}, Output ${fmtPct(r.profile.out_ratio, 2)}. Günstigster: <b>${esc(r.winner)}</b>${r.winner === cur ? ' (aktiv)' : cur ? ` · aktiv ist ${esc(cur)}` : ''}.</p>`;
  const rows = r.rows.slice(0, 10).map((x, i) => `<tr data-tag="${esc(x.tag)}"${x.tag === cur ? ' aria-current="true"' : ''} class="${i === 0 ? 'win' : ''}${x.tag === cur ? ' cur' : ''}"><td><span class="rkn">${i + 1}</span><span class="pn">${esc(x.tag)}</span> <span class="q">${esc(x.quant || '')}</span><span class="dbar" style="width:${clamp(x.delta, 0, 1) * 100}%"></span></td>` +
    `<td data-l="$/1M Prompt">${fmtPrice(x.unit)}</td><td data-l="Kosten">${fmtUsd(x.usd)}</td><td data-l="Δ">${i === 0 ? '–' : '+' + fmtPct(x.delta)}</td><td data-l="Input">${fmtPrice(x.in)}</td><td data-l="Cache">${fmtPrice(x.cr)}</td><td data-l="Output">${fmtPrice(x.out)}</td></tr>`).join('');
  el.innerHTML = html + `<table class="rank"><thead><tr><th>Anbieter</th><th>$/1M Prompt</th><th>Kosten</th><th title="Aufschlag gegenüber dem günstigsten Anbieter">Δ</th><th>Input</th><th>Cache</th><th>Output</th></tr></thead><tbody>${rows}</tbody></table><div class="note">Preise in $ pro 1M Token. Kosten = Prompt des Zeitraums × $/1M Prompt (inkl. Output-Anteil), Δ = Aufschlag zum günstigsten Anbieter. Klick auf einen Anbieter öffnet seinen Preisverlauf.</div>`;
  el.querySelectorAll('tr[data-tag]').forEach((tr) => tr.addEventListener('click', () => {
    const tag = tr.dataset.tag;
    if (D.watch && (D.watch.tags || []).some((t) => t.tag === tag)) {
      S.wfocus = tag;
      renderWatchSection();
      $('watch').scrollIntoView({ block: 'start' });
    }
    const x = watchDetail(D, tag);
    openDialog(x.title, x.html);
  }));
}

function renderForecast() {
  const F = D.forecast;
  const lastActual = F.series.filter((s) => s.kind === 'actual').length;
  const S0 = F.series.slice(Math.max(0, lastActual - 13));  // last 12 h of actual traffic + the 24 h forecast
  tabs($('fcTabs'), [['usd', 'Kosten pro Stunde'], ['tok', 'Token pro Stunde']], S.fc, (k) => { S.fc = k; renderForecast(); });
  const el = $('fcBody');
  if (!S0.length) { el.innerHTML = emptyBox('Keine Prognose verfügbar.'); return; }
  const nowH = S0.find((s) => s.kind === 'forecast') ? S0.filter((s) => s.kind === 'actual').slice(-1)[0].h : S0[S0.length - 1].h;
  const x0 = S0[0].h, x1 = S0[S0.length - 1].h;
  const W = width(el), H = W < 400 ? 190 : 230;
  const step = W < 500 ? 12 : 6;  // fewer tick labels on narrow screens
  const ticks = S0.filter((s) => new Date(s.h * 1000).getHours() % step === 0).map((s) => s.h);
  const common = { w: W, h: H, xMin: x0, xMax: x1, xTicks: ticks, xFmt: (t) => dayHour(t), nowX: nowH, shadeFrom: nowH };
  let chart, sum = '';
  if (S.fc === 'usd') {
    const line = (k) => S0.map((s) => [s.h, s[k]]);
    const cur = D.overview.current, best = D.overview.best;
    const same = Boolean(cur) && cur === best;  // then "aktuell" and "bester" are one line, not three legend entries
    const series = [{ cls: 'cur', points: line('usd_current') }];
    if (!same) series.push({ cls: 'best', dash: '2 4', points: line('usd_best') });
    if (F.alt) series.push({ cls: 'alt', dash: '7 4', points: line('usd_alt') });
    chart = lineChart(series, { ...common, yFmt: fmtUsd, label: 'Kosten pro Stunde, links Ist, rechts Prognose' });
    const fcs = S0.filter((s) => s.kind === 'forecast');
    const tot = (k) => fcs.reduce((a, s) => a + (s[k] || 0), 0);
    const chips = same
      ? `<span class="chip best">Router ist bereits optimal: ${esc(cur)} · <b>${fmtUsd(tot('usd_current'))}</b> in 24 h</span>`
      : `<span class="chip">aktuell ${esc(cur || '')}: <b>${fmtUsd(tot('usd_current'))}</b></span><span class="chip best">bester ${esc(best || '')}: <b>${fmtUsd(tot('usd_best'))}</b></span>`;
    sum = `<div class="chips" style="margin-top:8px">${chips}${F.alt ? `<span class="chip">Alternative ${esc(F.alt)}: <b>${fmtUsd(tot('usd_alt'))}</b></span>` : ''}</div><div class="note">Summe der nächsten 24 h. ${same ? 'Günstigster und aktiver Anbieter sind derselbe, deshalb eine Linie.' : 'Durchgezogen = aktuell, gepunktet = bester.'}${F.alt ? ' Gestrichelt = Alternative.' : ''} Jetzt-Linie und Schattierung trennen Ist von Prognose.</div>`;
  } else {
    chart = lineChart([{ cls: 'mea', points: S0.map((s) => [s.h, s.prompt]) }], { ...common, yFmt: fmtTok, label: 'Prompt-Token pro Stunde, links Ist, rechts Prognose' });
    sum = `<div class="note">Prognose = Durchschnitt derselben Stunde an den letzten Tagen.</div>`;
  }
  el.innerHTML = (chart || emptyBox('Noch keine Daten.')) + sum;
}

function logRow(x) {
  return x.type === 'switch'
    ? `<li><span class="ic" aria-hidden="true">⇄</span><div><b>Wechsel zu ${esc(x.to)}</b><div>${esc(x.text)}</div><time>${dayLabel(x.ts)} ${hhmm(x.ts)}</time></div></li>`
    : `<li><span class="ic price" aria-hidden="true">$</span><div><b>Preis-Flanke</b><div>${esc(describeFlank(x))}</div><time>${dayLabel(x.ts)} ${hhmm(x.ts)}</time></div></li>`;
}

function renderLog() {
  const items = [['all', 'Alles'], ['switch', 'Wechsel'], ['price', 'Preisänderungen']];
  tabs($('logTabs'), items, S.log, (k) => { S.log = k; renderLog(); });
  const rows = D.log.filter((x) => S.log === 'all' || x.type === S.log);
  const el = $('logBody');
  const runs = Number(D.freshness.runs_24h);
  const sum = `<div class="log-sum">${runs} Neuberechnungen in 24 h${D.freshness.first_decision ? ` · Aufzeichnung ${since()}` : ''} · ${D.log.filter((x) => x.type === 'switch').length} Wechsel, ${D.log.filter((x) => x.type === 'price').length} Preis-Flanken</div>`;
  if (!rows.length) {
    el.innerHTML = sum + `<ol class="timeline"><li><span class="ic" aria-hidden="true">–</span><div class="note">Noch keine Einträge dieser Art.</div></li></ol>`;
    return;
  }
  // Filter "Wechsel"/"Preisänderungen" is already short: show it directly. "Alles" keeps only the switches visible and puts the
  // long flank history behind one summary - 37 flanks next to 3 switches made the card 3400 px tall (UX review 2026-11-02).
  if (S.log !== 'all') {
    el.innerHTML = sum + `<ol class="timeline">${rows.slice(0, 40).map(logRow).join('')}</ol>`;
    return;
  }
  const switches = rows.filter((x) => x.type === 'switch').slice(0, 6);
  const allSwitches = D.log.filter((x) => x.type === 'switch');
  const allFlanks = D.log.filter((x) => x.type === 'price');
  const moreSwitches = Math.max(0, allSwitches.length - switches.length);
  const hidden = rows.filter((x) => !switches.includes(x)).slice(0, 60);
  el.innerHTML = sum +
    `<ol class="timeline">${switches.map(logRow).join('') || '<li><span class="ic" aria-hidden="true">–</span><div class="note">Noch kein Anbieterwechsel aufgezeichnet.</div></li>'}</ol>` +
    (hidden.length ? `<details class="log-all"><summary>Alle Ereignisse anzeigen (${allFlanks.length} Preis-Flanken${moreSwitches ? `, ${moreSwitches} weitere Wechsel` : ''})</summary><ol class="timeline">${hidden.map(logRow).join('')}</ol></details>` : '');
}

function renderWhatIf() {
  const P = D.overview.profile || {};
  const base = { tokensPerDay: Math.round((P.prompt_per_day || 1e8) / 1e6) * 1e6, hit: P.hit || 0.9, outRatio: P.out_ratio || 0.0025 };
  if (!S.wi) S.wi = { ...base };
  const w = S.wi;
  const el = $('wiBody');
  if (!D.providers.length) { el.innerHTML = emptyBox('Zurzeit ist kein zulässiger Anbieter bekannt, daher gibt es nichts zu rechnen.'); return; }
  el.innerHTML = `<div class="sliders">
    <div class="sl"><label for="wiTok">Prompt-Token/Tag <output id="oTok"></output></label><input id="wiTok" type="range" min="1" max="2000" step="1" aria-label="Millionen Prompt-Token pro Tag"></div>
    <div class="sl"><label for="wiHit">Cache-Quote <output id="oHit"></output></label><input id="wiHit" type="range" min="0" max="99" step="1"></div>
    <div class="sl"><label for="wiOut">Output-Anteil <output id="oOut"></output></label><input id="wiOut" type="range" min="0" max="100" step="1" aria-label="Output Promille"></div>
  </div><div id="wiRes"></div><div class="wi-actions"><button class="btn" id="wiReset">Auf gemessene Werte zurücksetzen</button><span class="wi-diff" id="wiDiff"></span></div>`;
  const t = $('wiTok'), h = $('wiHit'), o = $('wiOut');
  t.value = clamp(Math.round(w.tokensPerDay / 1e6), 1, 2000); h.value = Math.round(w.hit * 100); o.value = Math.round(w.outRatio * 1000);
  const upd = () => {
    w.tokensPerDay = Number(t.value) * 1e6; w.hit = Number(h.value) / 100; w.outRatio = Number(o.value) / 1000;
    $('oTok').textContent = fmtTok(w.tokensPerDay); $('oHit').textContent = fmtPct(w.hit); $('oOut').textContent = fmtPct(w.outRatio, 1);
    const res = whatIf(D.providers, w);
    const real = whatIf(D.providers, base);
    const same = res[0] && real[0] && res[0].tag === real[0].tag;
    $('wiRes').innerHTML = `<table class="rank"><thead><tr><th>Anbieter</th><th>$/1M Prompt</th><th>Kosten pro Tag</th><th>Δ</th></tr></thead><tbody>${res.slice(0, 6).map((x, i) => `<tr class="${i === 0 ? 'win' : ''}${x.tag === D.overview.current ? ' cur' : ''}"><td>${esc(x.tag)}</td><td data-l="$/1M">${fmtPrice(x.unit)}</td><td data-l="pro Tag">${fmtUsd(x.usd)}</td><td data-l="Δ">${i === 0 ? '–' : '+' + fmtPct(x.delta)}</td></tr>`).join('')}</tbody></table>`;
    let diff = same ? 'Gleicher Sieger wie mit den gemessenen Werten.' : `Anderer Sieger als mit den gemessenen Werten (${esc(real[0].tag)}).`;
    const a = D.providers.find((p) => p.tag === res[0].tag), c = D.providers.find((p) => p.tag === D.overview.current);
    const be = a && c && a.tag !== c.tag ? breakevenHit(a, c, w.outRatio) : null;
    if (be !== null) diff += ` Gleichstand mit ${esc(c.tag)} bei ${fmtPct(be)} Cache-Quote.`;
    $('wiDiff').textContent = ''; $('wiDiff').innerHTML = diff;
  };
  [t, h, o].forEach((i) => i.addEventListener('input', upd));
  $('wiReset').addEventListener('click', () => { S.wi = { ...base }; renderWhatIf(); });
  upd();
}

function renderHow() {
  const th = D.thresholds, x = explain('switching', th), tt = explain('cache_ttl', th);
  const warm = D.overview.warm || {};
  const tags = Object.keys(warm);
  const tbl = tags.length ? `<table class="warm-tbl"><thead><tr><th>Sitzung auf</th><th>bester ist günstiger um</th><th>Wechselkosten</th><th>Rücklauf</th><th>Entscheidung</th></tr></thead><tbody>${tags.map((t) => {
    const w = warm[t];
    return `<tr><td>${esc(t)}</td><td data-l="Vorsprung des Besten">${fmtPct(w.rel, 1)}</td><td data-l="Wechselkosten">${fmtUsd(w.penalty)}</td><td data-l="Rücklauf">${w.payback_min === null ? '–' : Math.round(w.payback_min) + ' min'}</td><td data-l="Entscheidung">${w.leave ? '<span class="pill watch">wechselt</span>' : 'bleibt'}</td></tr>`;
  }).join('')}</tbody></table><div class="note">Wechselrechnung für laufende Sitzungen mit heißem Cache ${info('warm', 'Wechselrechnung')}</div>` : '';
  $('howBody').innerHTML = `<ol class="steps">${x.body.map((p) => `<li>${esc(p.replace(/^\d+\.\s*/, ''))}</li>`).join('')}</ol><p class="note">${esc(tt.title)} ${info('cache_ttl', tt.title)}</p>${tbl}
    <p class="note">Schwellen: Vorsprung ≥ ${fmtPct(th.rel_gain_min)} · Rücklauf ≤ ${Number(th.payback_max_min)} min · Cache-Fenster ${Number(th.cache_ttl_min)} min · Abfrage alle ${Number(th.refresh_min)} min · Verfügbarkeit ≥ ${Number(th.min_uptime)} %</p>`;
}

function renderFoot() {
  $('modelName').textContent = `${D.model} · OpenRouter`;
  const mu = orModelUrl(D.model);
  if (mu) $('orModel').href = mu;
  $('foot').innerHTML = `Datenquelle: ${esc(D.node)}-Router, Preise live von openrouter.ai, Nutzung aus der lokalen opencode-Datenbank, Abrechnung aus dem OpenRouter-Schlüssel. Rein lesende Anzeige, es lässt sich nichts steuern. Es werden keine Schlüssel, Sitzungs-IDs oder Pfade veröffentlicht; vom Budget nur der verbleibende Rest. · <a href="./modelle/">Modelle & Rangliste</a> · <a href="./preis-uhr/">Preis-Uhr (Peak/Off-Peak)</a>`;
}

// explanation dialog (tap/click/Enter; Esc closes; works on touch, not hover-only)
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-explain]');
  if (!b || !D) return;
  const x = explain(b.dataset.explain, D.thresholds);
  openDialog(x.title, `<p><b>${esc(x.short)}</b></p>` + x.body.map((p) => `<p>${esc(p)}</p>`).join(''));
});
$('dlg').addEventListener('click', (e) => { if (e.target === $('dlg')) $('dlg').close(); });

let rz, lastW = 0;
new ResizeObserver(() => {  // charts are drawn at the container width: redraw only when the width changed (not on height changes from rendering)
  const w = document.body.clientWidth;
  if (w === lastW) return;
  lastW = w;
  clearTimeout(rz);
  rz = setTimeout(() => D && (renderControl(), renderForecast(), renderWatchSection()), 150);
}).observe(document.body);
load();
setInterval(load, 120000);
