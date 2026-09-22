import type { Problem } from '@/shared/http/exception';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS';

export type RetryPolicy = {
  readonly maxAttempts: number;
  readonly baseDelayMs: number;
  readonly methods: readonly HttpMethod[];
};

// NFR-REL-004: reads retry on 5xx/network errors with backoff, up to 3 attempts; mutations
// (POST/PUT/PATCH/DELETE) are never retried automatically, regardless of idempotency.
export const DEFAULT_RETRY_POLICY: RetryPolicy = {
  maxAttempts: 3,
  baseDelayMs: 200,
  methods: ['GET', 'HEAD', 'OPTIONS'],
};

export const isRetryable = (problem: Problem): boolean => {
  if (problem.code === 'network-error') {
    return true;
  }

  return problem.code === 'http-error' && (problem.status ?? 0) >= 500;
};
