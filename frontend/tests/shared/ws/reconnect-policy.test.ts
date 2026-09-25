import { describe, expect, it } from '@jest/globals';

import { reconnectDelayMs } from '@/shared/ws/reconnect-policy';

const seededRandom = (seed: number): (() => number) => {
  let state = seed >>> 0;

  return () => {
    state = (state * 1_664_525 + 1_013_904_223) >>> 0;
    return state / 2 ** 32;
  };
};

describe('reconnectDelayMs', () => {
  it('uses exponential backoff with deterministic jitter', () => {
    const random = seededRandom(1);

    expect(reconnectDelayMs(1, random())).toBeCloseTo(894.5822101086378, 10);
    expect(reconnectDelayMs(2, random())).toBeCloseTo(1895.416538976133, 10);
    expect(reconnectDelayMs(3, random())).toBeCloseTo(4006.7872516810894, 10);
  });

  it('never exceeds the maximum delay once jitter is applied', () => {
    expect(reconnectDelayMs(6, 0)).toBe(25_600);
    expect(reconnectDelayMs(10, 1)).toBe(30_000);
  });

  it('does not schedule an invalid or exhausted attempt', () => {
    const random = seededRandom(1);

    expect(reconnectDelayMs(0, random())).toBeUndefined();
    expect(reconnectDelayMs(11, random())).toBeUndefined();
    expect(reconnectDelayMs(1.5, random())).toBeUndefined();
    expect(reconnectDelayMs(1, -0.1)).toBeUndefined();
    expect(reconnectDelayMs(1, 1.1)).toBeUndefined();
  });
});