export type HeapSample = {
  readonly atMs: number
  readonly bytes: number
}

export type SoakCeiling = {
  /** Samples before this index are warmup (JIT, initial caches) and excluded from the verdict. */
  readonly warmupSamples: number
  /** Max allowed growth, in bytes, between the post-warmup baseline and the tail of the run. */
  readonly ceilingBytes: number
}

export class HeapGrowthExceeded extends Error {}

const mib = (bytes: number): string => `${(bytes / 1_048_576).toFixed(1)} MB`

// NFR-PERF-009: "no memory growth" over a 30-minute streaming session, with a stated ceiling — a
// few MB of GC noise is expected, a monotonic climb from a leaked subscription is not. Baseline
// and tail are each the mean of a half of the post-warmup samples, not the first/last single
// sample, so one GC pause either side can't flip the verdict.
export const assertBoundedGrowth = (samples: readonly HeapSample[], ceiling: SoakCeiling): void => {
  const usable = samples.slice(ceiling.warmupSamples)

  if (usable.length < 4) {
    throw new Error(`need at least 4 post-warmup samples to judge growth, got ${usable.length}`)
  }

  const half = Math.floor(usable.length / 2)
  const mean = (values: readonly HeapSample[]) =>
    values.reduce((sum, sample) => sum + sample.bytes, 0) / values.length

  const baseline = mean(usable.slice(0, half))
  const tail = mean(usable.slice(-half))
  const growth = tail - baseline

  if (growth > ceiling.ceilingBytes) {
    throw new HeapGrowthExceeded(
      `heap grew ${mib(growth)} (baseline ${mib(baseline)} -> tail ${mib(tail)}), ` +
        `ceiling is ${mib(ceiling.ceilingBytes)}`,
    )
  }
}
