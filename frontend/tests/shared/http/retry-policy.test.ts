import { describe, expect, it } from 'vitest';

import { httpErrorProblem, networkErrorProblem } from '@/shared/http/exception';
import { isRetryable } from '@/shared/http/retry-policy';

describe('isRetryable', () => {
  it('retries network errors', () => {
    expect(isRetryable(networkErrorProblem(new TypeError('fetch failed')))).toBe(true);
  });

  it('retries 5xx http errors', async () => {
    const response = new Response(null, { status: 503 });
    expect(isRetryable(await httpErrorProblem(response))).toBe(true);
  });

  it('does not retry 4xx http errors', async () => {
    const response = new Response(null, { status: 404 });
    expect(isRetryable(await httpErrorProblem(response))).toBe(false);
  });

  it('does not retry aborted requests', () => {
    expect(isRetryable({ code: 'aborted', title: 'Request aborted' })).toBe(false);
  });

  it('treats a missing status as below the retry threshold', () => {
    expect(isRetryable({ code: 'http-error', title: 'x' })).toBe(false);
  });
});
