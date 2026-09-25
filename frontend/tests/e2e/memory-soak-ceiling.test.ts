import { expect } from '@jest/globals'

import { assertBoundedGrowth, HeapGrowthExceeded, type HeapSample } from './helpers/memory-soak.js'

const sampleAt = (atMs: number, bytes: number): HeapSample => ({ atMs, bytes })

// Phase 2 accept criterion: "a deliberately leaked subscription fails [the soak]." Waiting out a
// real 30-minute run to prove the check is sensitive isn't practical, so this proves it against
// the ceiling-check logic itself: flat/noisy samples pass, a monotonic climb (what a leaked
// subscription, timer or observer produces) fails. tests/e2e/memory-soak.test.ts applies the same
// function to real heap samples from the built app.
describe('Memory soak ceiling check', function () {
  const ceiling = { warmupSamples: 2, ceilingBytes: 5 * 1_048_576 }

  it('passes when the heap stays flat (with GC noise) after warmup', () => {
    const samples = Array.from({ length: 12 }, (_, i) =>
      sampleAt(i * 1_000, 50 * 1_048_576 + (i % 2) * 1_048_576),
    )

    expect(() => assertBoundedGrowth(samples, ceiling)).not.toThrow()
  })

  it('fails when the heap climbs monotonically, as a leaked subscription would', () => {
    const samples = Array.from({ length: 12 }, (_, i) =>
      sampleAt(i * 1_000, 50 * 1_048_576 + i * 2 * 1_048_576),
    )

    expect(() => assertBoundedGrowth(samples, ceiling)).toThrow(HeapGrowthExceeded)
  })

  it('ignores growth confined to the warmup window', () => {
    const samples = [
      sampleAt(0, 40 * 1_048_576),
      sampleAt(1_000, 70 * 1_048_576),
      ...Array.from({ length: 10 }, (_, i) => sampleAt((i + 2) * 1_000, 70 * 1_048_576 + (i % 2) * 1_048_576)),
    ]

    expect(() => assertBoundedGrowth(samples, ceiling)).not.toThrow()
  })
})
