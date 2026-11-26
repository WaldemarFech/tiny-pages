/*! tiny-pages | Copyright 2026 Waldemar Fech | Apache-2.0 | https://github.com/WaldemarFech/tiny-pages */
// DeepSeek peak/off-peak logic.
// Peak is defined in UTC: Mon-Fri 01:00-04:00 and 06:00-10:00 UTC.
// Off-peak is half price. Chinese public holidays are off-peak all day
// (Beijing calendar day). Display timezone is Europe/Berlin and is resolved
// through Intl, so daylight-saving changes are automatic.

import { CN_HOLIDAYS } from './holidays.js?v=20261018c';

export const ZONE = 'Europe/Berlin';
export const PEAK_WINDOWS_UTC = [
  [1, 4],
  [6, 10],
];

const partsFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

const hmFmt = new Intl.DateTimeFormat('de-DE', {
  timeZone: ZONE,
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

const dayFmt = new Intl.DateTimeFormat('de-DE', {
  timeZone: ZONE,
  weekday: 'long',
});

const shortDayFmt = new Intl.DateTimeFormat('de-DE', {
  timeZone: ZONE,
  weekday: 'short',
});

const zoneNameFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: ZONE,
  timeZoneName: 'short',
});

const dateKeyFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

const beijingKeyFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

const utcClockFmt = new Intl.DateTimeFormat('de-DE', {
  timeZone: 'UTC',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

const changeDateFmt = new Intl.DateTimeFormat('de-DE', {
  timeZone: ZONE,
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
});

function zoneParts(date) {
  const out = {};
  for (const part of partsFmt.formatToParts(date)) {
    if (part.type !== 'literal') out[part.type] = Number(part.value);
  }
  return { y: out.year, m: out.month, d: out.day, H: out.hour, M: out.minute, S: out.second };
}

export function isChineseHoliday(date) {
  return Object.hasOwn(CN_HOLIDAYS, beijingKeyFmt.format(date));
}

export function chineseHolidayName(date) {
  const key = beijingKeyFmt.format(date);
  return Object.hasOwn(CN_HOLIDAYS, key) ? CN_HOLIDAYS[key] : null;
}

export function isPeak(date) {
  if (isChineseHoliday(date)) return false;
  const day = date.getUTCDay();
  if (day === 0 || day === 6) return false;
  const hour = date.getUTCHours() + date.getUTCMinutes() / 60 + date.getUTCSeconds() / 3600;
  return PEAK_WINDOWS_UTC.some(([from, to]) => hour >= from && hour < to);
}

export function priceLabel(date) {
  return isPeak(date) ? '100 %' : '50 %';
}

export function formatHM(date) {
  return hmFmt.format(date);
}

export function formatDay(date) {
  return dayFmt.format(date);
}

export function shortDay(date) {
  return shortDayFmt.format(date);
}

export function zoneAbbr(date) {
  const parts = zoneNameFmt.formatToParts(date);
  return parts.find((p) => p.type === 'timeZoneName')?.value ?? '';
}

export function localClock(date) {
  const p = zoneParts(date);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(p.H)}:${pad(p.M)}:${pad(p.S)}`;
}

export function localDateKey(date) {
  return dateKeyFmt.format(date);
}

export function utcClock(date) {
  return utcClockFmt.format(date);
}

export function utcOffsetHours(date) {
  return offsetAt(date) / 3600000;
}

export function utcOffsetLabel(date) {
  const hours = utcOffsetHours(date);
  const sign = hours < 0 ? '−' : '+';
  const abs = Math.abs(hours);
  const value = Number.isInteger(abs) ? String(abs) : `${Math.floor(abs)}:${String(Math.round((abs % 1) * 60)).padStart(2, '0')}`;
  return `UTC${sign}${value}`;
}

export function nextOffsetChange(date, horizonDays = 400) {
  const start = Math.floor(date.getTime() / 3600000) * 3600000;
  const current = offsetAt(new Date(start));
  for (let t = 3600000; t <= horizonDays * 24 * 3600000; t += 3600000) {
    const candidate = new Date(start + t);
    if (offsetAt(candidate) !== current) return candidate;
  }
  return null;
}

export function formatChangeDate(date) {
  return changeDateFmt.format(date);
}

// UTC instant of local 00:00 for the local calendar day containing `date`.
export function localDayStart(date) {
  const p = zoneParts(date);
  const wallMidnight = Date.UTC(p.y, p.m - 1, p.d);
  let instant = wallMidnight;
  for (let i = 0; i < 3; i += 1) {
    instant = wallMidnight - offsetAt(new Date(instant));
  }
  return new Date(instant);
}

function offsetAt(date) {
  const p = zoneParts(date);
  const wall = Date.UTC(p.y, p.m - 1, p.d, p.H, p.M, p.S) + date.getMilliseconds();
  return wall - date.getTime();
}

export function localNoon(date) {
  return new Date(localDayStart(date).getTime() + 12 * 3600 * 1000);
}

// Splits [dayStart, dayEnd) into contiguous peak/off-peak segments.
export function daySegments(dayStart, dayEnd) {
  const startMs = dayStart.getTime();
  const endMs = dayEnd.getTime();
  const span = endMs - startMs;
  const segments = [];
  let current = { start: startMs, peak: isPeak(dayStart) };
  for (let t = startMs + 60000; t < endMs; t += 60000) {
    const peak = isPeak(new Date(t));
    if (peak !== current.peak) {
      segments.push({ ...current, end: t });
      current = { start: t, peak };
    }
  }
  segments.push({ ...current, end: endMs });
  return segments.map((s) => ({
    start: s.start,
    end: s.end,
    peak: s.peak,
    startFrac: (s.start - startMs) / span,
    endFrac: (s.end - startMs) / span,
    label: `${formatHM(new Date(s.start))}–${formatHM(new Date(s.end))}`,
  }));
}

export function weekModel(now) {
  const days = [];
  const todayKey = localDateKey(now);
  let anchor = localNoon(now);
  for (let i = 0; i < 7; i += 1) {
    const dayStart = localDayStart(anchor);
    const dayEnd = localDayStart(new Date(anchor.getTime() + 24 * 3600 * 1000));
    const segments = daySegments(dayStart, dayEnd);
    days.push({
      anchor,
      key: localDateKey(anchor),
      short: shortDay(anchor),
      long: formatDay(anchor),
      isToday: localDateKey(anchor) === todayKey,
      holiday: chineseHolidayName(anchor),
      segments,
      peakLabels: segments.filter((s) => s.peak).map((s) => s.label),
    });
    anchor = new Date(anchor.getTime() + 24 * 3600 * 1000);
  }
  return days;
}

// Finds the next state change within the horizon (minute precision).
export function nextTransition(now, horizonHours = 24 * 8) {
  const from = Math.floor(now.getTime() / 60000) * 60000;
  const state = isPeak(new Date(from));
  for (let t = 60000; t <= horizonHours * 3600 * 1000; t += 60000) {
    const at = new Date(from + t);
    const peak = isPeak(at);
    if (peak !== state) return { at, peak, fromPeak: state };
  }
  return null;
}

export function durationText(ms) {
  const totalMinutes = Math.max(0, Math.round(ms / 60000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes} min`;
  if (minutes === 0) return `${hours} h`;
  return `${hours} h ${minutes} min`;
}
