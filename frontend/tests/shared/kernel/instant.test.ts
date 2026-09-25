import { describe, expect, it } from '@jest/globals';

import { CalendarDate, FixedClock, Instant } from '@/shared/kernel/instant';
import { isErr, isOk } from '@/shared/kernel/result';

describe('time value objects', () => {
  it('compares Instants by their epoch milliseconds', () => {
    const first = Instant.fromIso('2026-08-13T10:00:00.000Z');
    const same = Instant.fromEpochMilliseconds(1_786_615_200_000);
    const later = Instant.fromIso('2026-08-13T10:00:01.000Z');

    expect(isOk(first)).toBe(true);
    expect(isOk(same)).toBe(true);
    expect(isOk(later)).toBe(true);
    if (!isOk(first) || !isOk(same) || !isOk(later)) {
      return;
    }

    expect(first.value.equals(same.value)).toBe(true);
    expect(first.value.compare(later.value)).toBe(-1);
    expect(later.value.compare(first.value)).toBe(1);
    expect(first.value.compare(same.value)).toBe(0);
    expect(later.value.toIso()).toBe('2026-08-13T10:00:01.000Z');
  });

  it('validates and orders CalendarDates', () => {
    const earlier = CalendarDate.fromIso('2026-02-28');
    const later = CalendarDate.fromIso('2026-03-01');

    expect(isOk(earlier)).toBe(true);
    expect(isOk(later)).toBe(true);
    expect(isErr(CalendarDate.fromIso('2026-02-29'))).toBe(true);
    expect(isErr(CalendarDate.fromIso('not-a-date'))).toBe(true);
    if (!isOk(earlier) || !isOk(later)) {
      return;
    }

    expect(earlier.value.compare(later.value)).toBe(-1);
    expect(later.value.compare(earlier.value)).toBe(1);
    expect(later.value.equals(later.value)).toBe(true);
    expect(earlier.value.toIso()).toBe('2026-02-28');
  });

  it('returns the configured instant from a FixedClock', () => {
    const instant = Instant.fromIso('2026-08-13T10:00:00.000Z');
    if (!isOk(instant)) {
      return;
    }

    const clock = new FixedClock(instant.value);
    expect(clock.now()).toBe(instant.value);
    expect(clock.now().toIso()).toBe('2026-08-13T10:00:00.000Z');
  });
});