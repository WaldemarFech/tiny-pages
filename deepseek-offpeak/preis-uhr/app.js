/*! tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages */
import {
  daySegments, durationText, formatChangeDate, formatHM, isPeak, localClock,
  localDayStart, localDateKey, localNoon, nextOffsetChange, nextTransition,
  shortDay, utcClock, utcOffsetLabel, weekModel, zoneAbbr,
} from './peak.js?v=20261018c';

const nowCard = document.getElementById('nowCard');
const statusText = document.getElementById('statusText');
const clockEl = document.getElementById('clock');
const utcEl = document.getElementById('utcClock');
const whenEl = document.getElementById('when');
const hintEl = document.getElementById('hint');
const todayBar = document.getElementById('todayBar');
const todayName = document.getElementById('todayName');
const weekRows = document.getElementById('weekRows');
const zoneAbbrEl = document.getElementById('zoneAbbr');
const detailEl = document.getElementById('detail');
const priceCells = Array.from(document.querySelectorAll('[data-col]'));

const PRICE_NOTE = {
  peak: 'Peak · $1.20 / 1M Output',
  off: 'Off-Peak · $0.60 / 1M Output',
};

const DAY_MS = 24 * 3600 * 1000;
const todayFmt = new Intl.DateTimeFormat('de-DE', {
  timeZone: 'Europe/Berlin', weekday: 'long', day: '2-digit', month: '2-digit',
});

let currentDayKey = '';
let currentWeekKey = '';
let dayStartMs = 0;
let dayEndMs = 0;
let todayRowBar = null;

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function renderSegments(container, segments) {
  for (const s of segments) {
    const seg = el('div', `seg ${s.peak ? 'peak' : 'off'}`);
    seg.style.left = `${s.startFrac * 100}%`;
    seg.style.width = `${(s.endFrac - s.startFrac) * 100}%`;
    seg.title = `${s.label} · ${s.peak ? PRICE_NOTE.peak : PRICE_NOTE.off}`;
    container.appendChild(seg);
  }
}

function addMark(container) {
  const mark = el('div', 'nowmark');
  container.appendChild(mark);
  return mark;
}

function fraction(now) {
  return (now.getTime() - dayStartMs) / (dayEndMs - dayStartMs);
}

function buildToday(now) {
  const start = localDayStart(now);
  const end = localDayStart(new Date(start.getTime() + DAY_MS));
  dayStartMs = start.getTime();
  dayEndMs = end.getTime();
  todayBar.textContent = '';
  renderSegments(todayBar, daySegments(start, end));
  addMark(todayBar);
  todayName.textContent = todayFmt.format(now);

  const change = nextOffsetChange(now);
  const changeText = change
    ? ` · nächste Umstellung ${formatChangeDate(change)} → ${utcOffsetLabel(change)}`
    : '';
  detailEl.textContent = `Europe/Berlin = ${utcOffsetLabel(now)} (${zoneAbbr(now)})${changeText}`;
}

function buildWeek(now) {
  const model = weekModel(now);
  weekRows.textContent = '';
  todayRowBar = null;
  for (const day of model) {
    const row = el('div', `week-row${day.isToday ? ' today' : ''}`);
    const label = el('div', 'day');
    label.textContent = day.isToday ? 'Heute' : day.short;
    label.title = day.long;
    if (day.holiday) {
      label.textContent += '*';
      label.title += ` · ${day.holiday}`;
      row.classList.add('holiday');
    }
    const bar = el('div', 'week-bar');
    renderSegments(bar, day.segments);
    if (day.isToday) {
      todayRowBar = bar;
      addMark(bar);
    }
    row.append(label, bar);
    weekRows.appendChild(row);
  }
}

function tick() {
  const now = new Date();
  const peak = isPeak(now);
  const next = nextTransition(now);

  const dayKey = localDateKey(now);
  if (dayKey !== currentDayKey) {
    currentDayKey = dayKey;
    buildToday(now);
    currentWeekKey = '';
  }
  const weekKey = localDateKey(localNoon(now));
  if (weekKey !== currentWeekKey) {
    currentWeekKey = weekKey;
    buildWeek(now);
  }

  clockEl.textContent = localClock(now);
  utcEl.textContent = utcClock(now);
  zoneAbbrEl.textContent = zoneAbbr(now);

  nowCard.classList.toggle('peak', peak);
  nowCard.classList.toggle('off', !peak);
  statusText.textContent = peak ? 'JETZT TEUER' : 'JETZT GÜNSTIG';
  for (const cell of priceCells) {
    cell.classList.toggle('active', (cell.dataset.col === 'peak') === peak);
  }

  if (next) {
    const left = durationText(next.at.getTime() - now.getTime());
    const at = `${shortDay(next.at)} ${formatHM(next.at)}`;
    if (peak) {
      whenEl.innerHTML = `Noch <b>${left}</b> voller Preis · ab <b>${at}</b> wieder halber Preis`;
      hintEl.textContent = 'Wenn es nicht dringend ist: kurz warten oder Arbeit beenden.';
    } else {
      whenEl.innerHTML = `Noch <b>${left}</b> halber Preis · ab <b>${at}</b> voller Preis`;
      hintEl.textContent = 'Gutes Fenster für lange Sessions.';
    }
  } else {
    whenEl.textContent = 'Kein Wechsel in den nächsten 8 Tagen.';
    hintEl.textContent = '';
  }

  const pos = `${fraction(now) * 100}%`;
  const todayMark = todayBar.querySelector('.nowmark');
  if (todayMark) todayMark.style.left = pos;
  if (todayRowBar) {
    const mark = todayRowBar.querySelector('.nowmark');
    if (mark) mark.style.left = pos;
  }
}

tick();
setInterval(tick, 1000);
