import { expect } from '@jest/globals'
import type { MatcherFunction } from 'expect'

// Two Vitest matchers the suites lean on that Jest does not ship.

const toHaveBeenCalledExactlyOnceWith: MatcherFunction<unknown[]> = function (received, ...expected) {
  const calls = (received as { mock?: { calls?: unknown[][] } } | undefined)?.mock?.calls
  if (!Array.isArray(calls)) {
    throw new TypeError(`${this.utils.matcherHint('toHaveBeenCalledExactlyOnceWith')}\n\nreceived value must be a mock or spy function`)
  }

  const pass = calls.length === 1 && this.equals(calls[0], expected)

  return {
    pass,
    message: () =>
      `${this.utils.matcherHint('toHaveBeenCalledExactlyOnceWith', undefined, undefined, { isNot: this.isNot === true })}\n\n` +
      `Expected: ${this.isNot ? 'not ' : ''}called exactly once with ${this.utils.printExpected(expected)}\n` +
      `Received: ${calls.length} call(s)${calls.length > 0 ? ` ${this.utils.printReceived(calls)}` : ''}`,
  }
}

type TypeofResult = 'bigint' | 'boolean' | 'function' | 'number' | 'object' | 'string' | 'symbol' | 'undefined'

const toBeTypeOf: MatcherFunction<[expected: TypeofResult]> = function (received, expected) {
  const actual = typeof received

  return {
    pass: actual === expected,
    message: () =>
      `${this.utils.matcherHint('toBeTypeOf', undefined, undefined, { isNot: this.isNot === true })}\n\n` +
      `Expected: ${this.isNot ? 'not ' : ''}${this.utils.printExpected(expected)}\n` +
      `Received: ${this.utils.printReceived(actual)}`,
  }
}

expect.extend({ toHaveBeenCalledExactlyOnceWith, toBeTypeOf })

declare module 'expect' {
  interface Matchers<R> {
    toHaveBeenCalledExactlyOnceWith(...expected: unknown[]): R
    toBeTypeOf(expected: TypeofResult): R
  }
}
