import { err, isOk, ok, type Result } from '@/shared/kernel/result';

export type InvalidInstant = {
  readonly kind: 'invalid-instant';
  readonly input: string | number;
};

export type InvalidCalendarDate = {
  readonly kind: 'invalid-calendar-date';
  readonly input: string;
};

const compareNumbers = (left: number, right: number): -1 | 0 | 1 => {
  if (left < right) {
    return -1;
  }

  return left > right ? 1 : 0;
};

export class Instant {
  readonly epochMilliseconds: number;

  // The one Instant obtainable without a Result: a total ordering needs a floor for values that
  // failed to parse, and every factory is fallible because its input is not.
  static readonly EPOCH = new Instant(0);

  private constructor(epochMilliseconds: number) {
    this.epochMilliseconds = epochMilliseconds;
  }

  static fromIso(input: string): Result<Instant, InvalidInstant> {
    return Instant.fromEpochMilliseconds(Date.parse(input), input);
  }

  static fromEpochMilliseconds(
    epochMilliseconds: number,
    input: string | number = epochMilliseconds,
  ): Result<Instant, InvalidInstant> {
    return Number.isFinite(epochMilliseconds)
      ? ok(new Instant(epochMilliseconds))
      : err({ kind: 'invalid-instant', input });
  }

  equals(other: Instant): boolean {
    return this.epochMilliseconds === other.epochMilliseconds;
  }

  compare(other: Instant): -1 | 0 | 1 {
    return compareNumbers(this.epochMilliseconds, other.epochMilliseconds);
  }

  toIso(): string {
    return new Date(this.epochMilliseconds).toISOString();
  }
}

const calendarDatePattern = /^(\d{4})-(\d{2})-(\d{2})$/;

export class CalendarDate {
  readonly value: string;

  private constructor(value: string) {
    this.value = value;
  }

  static fromIso(input: string): Result<CalendarDate, InvalidCalendarDate> {
    const match = calendarDatePattern.exec(input);
    const yearText = match?.[1];
    const monthText = match?.[2];
    const dayText = match?.[3];

    if (yearText === undefined || monthText === undefined || dayText === undefined) {
      return err({ kind: 'invalid-calendar-date', input });
    }

    const year = Number(yearText);
    const month = Number(monthText);
    const day = Number(dayText);
    const date = new Date(Date.UTC(year, month - 1, day));

    if (
      date.getUTCFullYear() !== year ||
      date.getUTCMonth() !== month - 1 ||
      date.getUTCDate() !== day
    ) {
      return err({ kind: 'invalid-calendar-date', input });
    }

    return ok(new CalendarDate(input));
  }

  equals(other: CalendarDate): boolean {
    return this.value === other.value;
  }

  compare(other: CalendarDate): -1 | 0 | 1 {
    return this.value < other.value ? -1 : this.value > other.value ? 1 : 0;
  }

  toIso(): string {
    return this.value;
  }
}

export interface Clock {
  now(): Instant;
}

// The only reader of the wall clock (STD-001 §6: no bare `Date.now()` outside a Clock).
export class SystemClock implements Clock {
  now(): Instant {
    const instant = Instant.fromEpochMilliseconds(Date.now());

    if (!isOk(instant)) {
      throw new Error('Date.now() returned a non-finite value');
    }

    return instant.value;
  }
}

export class FixedClock implements Clock {
  private readonly instant: Instant;

  constructor(instant: Instant) {
    this.instant = instant;
  }

  now(): Instant {
    return this.instant;
  }
}