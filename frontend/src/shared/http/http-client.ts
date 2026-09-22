import { err, isErr, ok, type Result } from '@/shared/kernel/result';
import type { Logger } from '@/shared/logging/logger';

import { abortedProblem, httpErrorProblem, networkErrorProblem, type Problem } from '@/shared/http/exception';
import { DEFAULT_RETRY_POLICY, isRetryable, type HttpMethod, type RetryPolicy } from '@/shared/http/retry-policy';

export type HttpRequestInit = {
  method?: HttpMethod;
  headers?: HeadersInit;
  body?: BodyInit;
  signal?: AbortSignal;
  credentials?: RequestCredentials;
  retry?: RetryPolicy;
  logger?: Logger;
};

// Fetch wrapper (T-007): same-origin credentials, abortable, Problem-normalised, retries
// idempotent reads only. Never throws; failures are `err(Problem)`.
export class HttpClient {
  async request<T = unknown>(
    input: string,
    init: HttpRequestInit = {},
  ): Promise<Result<T, Problem>> {
    const method = init.method ?? 'GET';
    const policy = init.retry ?? DEFAULT_RETRY_POLICY;
    const maxAttempts = policy.methods.includes(method) ? policy.maxAttempts : 1;

    let attempt = 1;
    let result = await this.performRequest<T>(input, method, init);

    while (isErr(result) && attempt < maxAttempts && isRetryable(result.error)) {
      await this.delay(policy.baseDelayMs * attempt);
      attempt += 1;
      result = await this.performRequest<T>(input, method, init);
    }

    if (isErr(result)) {
      this.logFailure(init.logger, method, input, result.error);
    }

    return result;
  }

  private async performRequest<T>(
    input: string,
    method: HttpMethod,
    init: HttpRequestInit,
  ): Promise<Result<T, Problem>> {
    let response: Response;

    try {
      response = await fetch(input, {
        ...(init.headers !== undefined ? { headers: init.headers } : {}),
        ...(init.body !== undefined ? { body: init.body } : {}),
        ...(init.signal !== undefined ? { signal: init.signal } : {}),
        method,
        credentials: init.credentials ?? 'same-origin', // ADR-006
      });
    } catch (cause) {
      return err(this.isAbort(cause, init.signal) ? abortedProblem() : networkErrorProblem(cause));
    }

    if (!response.ok) {
      return err(await httpErrorProblem(response));
    }

    return ok(await this.readSuccessBody<T>(response));
  }

  private isAbort(cause: unknown, signal: AbortSignal | undefined): boolean {
    return signal?.aborted === true || (cause instanceof DOMException && cause.name === 'AbortError');
  }

  // Empty/204 responses resolve to `undefined`; JSON bodies parse per their content-type;
  // anything else is returned as text rather than assumed to be JSON (FIX-07).
  private async readSuccessBody<T>(response: Response): Promise<T> {
    if (response.status === 204 || response.headers.get('content-length') === '0') {
      return undefined as T;
    }

    const contentType = response.headers.get('content-type') ?? '';
    if (contentType.includes('json')) {
      return (await response.json()) as T;
    }

    return (await response.text()) as unknown as T;
  }

  private delay(milliseconds: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  private logFailure(
    logger: Logger | undefined,
    method: HttpMethod,
    input: string,
    problem: Problem,
  ): void {
    logger?.warn('http_request_failed', {
      method,
      url: input,
      code: problem.code,
      ...(problem.status !== undefined ? { status: problem.status } : {}),
    });
  }
}
