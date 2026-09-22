import { describe, expect, it } from 'vitest';

import { andThen, combine, err, isErr, isOk, map, mapErr, ok, unwrapOr } from '@/shared/kernel/result';

describe('Result combinators', () => {
  it('maps successful values and preserves errors', () => {
    expect(map(ok(2), (value) => value * 2)).toEqual(ok(4));
    expect(map(err('missing'), (value: number) => value * 2)).toEqual(err('missing'));
    expect(mapErr(err('missing'), (error) => error.toUpperCase())).toEqual(err('MISSING'));
    expect(mapErr(ok(2), (error: string) => error.toUpperCase())).toEqual(ok(2));
  });

  it('chains successful values without invoking later transformations after an error', () => {
    expect(andThen(ok(2), (value) => ok(value * 2))).toEqual(ok(4));
    expect(andThen(err('missing'), (value: number) => ok(value * 2))).toEqual(err('missing'));
  });

  it('narrows results and supplies a fallback', () => {
    const successful = ok(2);
    const failed = err('missing');

    expect(isOk(successful)).toBe(true);
    expect(isErr(failed)).toBe(true);
    expect(unwrapOr(successful, 0)).toBe(2);
    expect(unwrapOr(failed, 0)).toBe(0);
  });

  it('combines values and returns the first error without reading later results', () => {
    const firstError = err('first');
    const laterError = err('later');

    expect(combine([ok(1), ok(2)])).toEqual(ok([1, 2]));
    expect(combine([ok(1), firstError, laterError])).toBe(firstError);
  });
});