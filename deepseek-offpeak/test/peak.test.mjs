import test from 'node:test';
import assert from 'node:assert/strict';

import {
  chineseHolidayName, daySegments, durationText, formatHM, isPeak, localClock,
  localDateKey, localDayStart, localNoon, nextOffsetChange, nextTransition,
  utcOffsetHours, utcOffsetLabel, weekModel,
} from '../preis-uhr/peak.js';

const utc = (iso) => new Date(iso);

test('peak windows follow UTC weekday hours', () => {
  assert.equal(isPeak(utc('2026-10-19T00:30:00Z')), false, 'Mon 00:30 UTC off');
  assert.equal(isPeak(utc('2026-10-19T01:00:00Z')), true, 'Mon 01:00 UTC peak');
  assert.equal(isPeak(utc('2026-10-19T03:59:59Z')), true, 'Mon 03:59:59 UTC peak');
  assert.equal(isPeak(utc('2026-10-19T04:00:00Z')), false, 'Mon 04:00 UTC off');
  assert.equal(isPeak(utc('2026-10-19T05:59:00Z')), false, 'Mon 05:59 UTC off');
  assert.equal(isPeak(utc('2026-10-19T06:00:00Z')), true, 'Mon 06:00 UTC peak');
  assert.equal(isPeak(utc('2026-10-19T09:59:59Z')), true, 'Mon 09:59:59 UTC peak');
  assert.equal(isPeak(utc('2026-10-19T10:00:00Z')), false, 'Mon 10:00 UTC off');
});

test('weekends are off-peak all day', () => {
  assert.equal(isPeak(utc('2026-10-17T02:30:00Z')), false, 'Sat 02:30 UTC');
  assert.equal(isPeak(utc('2026-10-17T08:00:00Z')), false, 'Sat 08:00 UTC');
  assert.equal(isPeak(utc('2026-10-18T07:00:00Z')), false, 'Sun 07:00 UTC');
});

test('Friday ends at 10:00 UTC', () => {
  assert.equal(isPeak(utc('2026-10-23T09:59:00Z')), true);
  assert.equal(isPeak(utc('2026-10-23T10:00:00Z')), false);
});

test('next transition is found with minute precision', () => {
  const a = nextTransition(utc('2026-10-19T03:30:00Z'));
  assert.equal(a.at.toISOString(), '2026-10-19T04:00:00.000Z');
  assert.equal(a.peak, false);

  const b = nextTransition(utc('2026-10-19T11:00:00Z'));
  assert.equal(b.at.toISOString(), '2026-10-20T01:00:00.000Z');
  assert.equal(b.peak, true);

  const c = nextTransition(utc('2026-10-23T11:00:00Z'));
  assert.equal(c.at.toISOString(), '2026-10-26T01:00:00.000Z');
  assert.equal(c.peak, true);
});

test('Berlin local time follows CET/CEST', () => {
  assert.equal(formatHM(utc('2026-10-19T01:30:00Z')), '03:30', 'CEST is UTC+2');
  assert.equal(formatHM(utc('2026-10-26T01:30:00Z')), '02:30', 'CET is UTC+1');
  assert.equal(localClock(utc('2026-10-19T01:05:09Z')), '03:05:09');
  assert.equal(localDateKey(utc('2026-10-18T23:30:00Z')), '2026-10-19');
});

test('local day boundaries handle the DST switch', () => {
  const noon = localNoon(utc('2026-10-25T12:00:00Z'));
  const start = localDayStart(noon);
  const next = localDayStart(new Date(noon.getTime() + 24 * 3600 * 1000));
  assert.equal(start.toISOString(), '2026-10-24T22:00:00.000Z');
  assert.equal(next.toISOString(), '2026-10-25T23:00:00.000Z');
  assert.equal((next.getTime() - start.getTime()) / 3600000, 25, '2026-10-25 has 25 local hours');
});

test('Monday renders as 03-06 and 08-12 local peak', () => {
  const now = utc('2026-10-19T10:00:00Z');
  const model = weekModel(now);
  const monday = model.find((d) => d.key === '2026-10-19');
  assert.deepEqual(monday.peakLabels, ['03:00–06:00', '08:00–12:00']);
  assert.equal(monday.segments.length, 5);
  assert.equal(monday.segments[0].peak, false);
  assert.equal(monday.segments[1].peak, true);
});

test('Sunday has no peak segments', () => {
  const model = weekModel(utc('2026-10-18T12:00:00Z'));
  const sunday = model.find((d) => d.key === '2026-10-18');
  assert.deepEqual(sunday.peakLabels, []);
  assert.equal(sunday.isToday, true);
});

test('segments cover the full day without gaps', () => {
  const start = localDayStart(localNoon(utc('2026-10-19T10:00:00Z')));
  const end = localDayStart(new Date(start.getTime() + 24 * 3600 * 1000));
  const segments = daySegments(start, end);
  assert.equal(segments[0].start, start.getTime());
  assert.equal(segments.at(-1).end, end.getTime());
  for (let i = 1; i < segments.length; i += 1) {
    assert.equal(segments[i].start, segments[i - 1].end);
  }
});

test('Chinese public holidays remove peak hours', () => {
  assert.equal(isPeak(utc('2026-10-01T02:00:00Z')), false, 'National Day 02:00 UTC');
  assert.equal(isPeak(utc('2026-10-01T08:00:00Z')), false, 'National Day 08:00 UTC');
  assert.equal(isPeak(utc('2026-09-30T02:00:00Z')), true, 'day before National Day');
  assert.equal(isPeak(utc('2026-10-08T02:00:00Z')), true, 'day after National Day');
  assert.equal(isPeak(utc('2026-02-18T02:30:00Z')), false, 'Spring Festival');
});

test('holiday names use the Beijing calendar day', () => {
  assert.equal(chineseHolidayName(utc('2026-02-18T04:00:00Z')), '春节');
  assert.equal(chineseHolidayName(utc('2026-10-01T20:00:00Z')), '国庆节');
  assert.equal(chineseHolidayName(utc('2026-10-09T02:00:00Z')), null);
});

test('UTC offset and the next DST change', () => {
  assert.equal(utcOffsetHours(utc('2026-10-19T12:00:00Z')), 2);
  assert.equal(utcOffsetHours(utc('2026-10-26T12:00:00Z')), 1);
  assert.equal(utcOffsetLabel(utc('2026-10-19T12:00:00Z')), 'UTC+2');
  assert.equal(utcOffsetLabel(utc('2026-10-26T12:00:00Z')), 'UTC+1');
  assert.equal(nextOffsetChange(utc('2026-10-19T12:00:00Z')).toISOString(), '2026-10-25T01:00:00.000Z');
});

test('holiday weekdays show no peak segments', () => {
  const model = weekModel(utc('2026-10-01T10:00:00Z'));
  const thursday = model.find((d) => d.key === '2026-10-01');
  assert.equal(thursday.holiday, '国庆节');
  assert.deepEqual(thursday.peakLabels, []);
});

test('duration text is compact', () => {
  assert.equal(durationText(0), '0 min');
  assert.equal(durationText(45 * 60000), '45 min');
  assert.equal(durationText(2 * 3600000), '2 h');
  assert.equal(durationText(90 * 60000), '1 h 30 min');
});
