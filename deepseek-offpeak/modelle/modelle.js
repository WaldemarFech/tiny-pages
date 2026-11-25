// Modelle page: OpenRouter-Rangliste + kostenlose Modelle (moved off the Router page 2026-11-01). Same read-only rules:
// trending.json carries the leaderboard, router.json only marks our model and the profile used for "Meine Top 3".
// This module lives in /modelle/, so every shared asset goes through the parent path.
import { ago, errorNotice, orModelUrl, tsSec } from '../model.js?v=20261102d';
import { renderFree, renderMarket } from '../trending.js?v=20261102d';
import { explain } from '../explain.js?v=20261102d';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&#34;', "'": '&#39;' }[c]));

let D = null;
let TR = null;

async function loadRouter() {
  try {
    const r = await fetch(`../router.json?t=${Date.now()}`, { cache: 'no-store' });
    D = r.ok ? await r.json() : null;
  } catch (e) { D = null; }
}

async function loadTrending() {
  try {
    const r = await fetch(`../models_trending.json?t=${Date.now()}`, { cache: 'no-store' });
    TR = r.ok ? await r.json() : null;
  } catch (e) { TR = null; }
  renderMarket($('marketBody'), TR, D);
  renderFree($('freeBody'), $('alertBar'), TR);
  const fresh = [];
  const trAge = TR ? tsSec(TR.generated_at) : null;
  if (TR) fresh.push(`Rangliste <b>${trAge === null ? 'Zeit unbekannt' : ago(trAge, Date.now() / 1000)}</b>`);
  else fresh.push('Rangliste nicht verfügbar');
  if (D) fresh.push(`Router <b>${ago(tsSec(D.generated_at), Date.now() / 1000)}</b>${D.error ? ` · <b class="warn-text">${esc(errorNotice(D, Date.now() / 1000))}</b>` : ''}`);
  $('fresh').innerHTML = fresh.join(' · ');
}

function renderFoot() {
  const mu = D ? orModelUrl(D.model) : null;
  if (mu) $('orModel').href = mu;
  $('foot').innerHTML = `Datenquelle: OpenRouter (Modelle, Preise) plus der ${D ? esc(D.node) : 'lokale'}-Router für die Markierung „unser Modell“. Rein lesende Anzeige. Es werden keine Schlüssel, Sitzungs-IDs oder Pfade veröffentlicht. · <a href="/">Provider-Router</a> · <a href="/preis-uhr/">Preis-Uhr (Peak/Off-Peak)</a>`;
}

// explanation dialog (same behaviour as the router page)
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-explain]');
  if (!b || !D) return;
  const x = explain(b.dataset.explain, (D && D.thresholds) || {});
  $('dlgTitle').textContent = x.title;
  $('dlgBody').innerHTML = `<p><b>${esc(x.short)}</b></p>` + x.body.map((p) => `<p>${esc(p)}</p>`).join('');
  $('dlg').showModal();
});
$('dlg').addEventListener('click', (e) => { if (e.target === $('dlg')) $('dlg').close(); });

loadRouter().then(() => { loadTrending(); renderFoot(); });
setInterval(() => { loadRouter().then(loadTrending); }, 120000);
