import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { isErr, isOk } from '@/shared/kernel/result';

import { HttpClient } from '@/shared/http/http-client';

const BASE = 'https://wla.test';
const client = new HttpClient();

// Minimal local MSW server for this module; Task 07 will fold this into the shared
// tests/msw/ harness once it lands (see XMGPLAT-11335 project context).
const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('HttpClient', () => {
  it('returns ok(T) for a JSON success response', async () => {
    server.use(http.get(`${BASE}/things/1`, () => HttpResponse.json({ id: 1 })));

    const result = await client.request<{ id: number }>(`${BASE}/things/1`);

    expect(isOk(result)).toBe(true);
    expect(result).toEqual({ kind: 'ok', value: { id: 1 } });
  });

  it('returns ok(undefined) for an empty 204', async () => {
    server.use(http.delete(`${BASE}/things/1`, () => new HttpResponse(null, { status: 204 })));

    const result = await client.request(`${BASE}/things/1`, { method: 'DELETE' });

    expect(result).toEqual({ kind: 'ok', value: undefined });
  });

  it('normalises an HTML 502 into a Problem instead of a JSON parse error', async () => {
    server.use(
      http.get(
        `${BASE}/flaky`,
        () =>
          new HttpResponse('<html>bad gateway</html>', {
            status: 502,
            statusText: 'Bad Gateway',
            headers: { 'content-type': 'text/html' },
          }),
      ),
    );

    const result = await client.request(`${BASE}/flaky`, {
      retry: { maxAttempts: 1, baseDelayMs: 0, methods: [] },
    });

    expect(result).toEqual({
      kind: 'err',
      error: { code: 'http-error', status: 502, title: 'Bad Gateway' },
    });
  });

  it('maps a network failure to a network-error Problem, distinguishable from an abort', async () => {
    server.use(http.get(`${BASE}/unreachable`, () => HttpResponse.error()));

    const result = await client.request(`${BASE}/unreachable`, {
      retry: { maxAttempts: 1, baseDelayMs: 0, methods: [] },
    });

    expect(isErr(result)).toBe(true);
    if (isErr(result)) {
      expect(result.error.code).toBe('network-error');
    }
  });

  it('maps an aborted request to a distinct aborted Problem', async () => {
    server.use(
      http.get(`${BASE}/slow`, async () => {
        await new Promise((resolve) => setTimeout(resolve, 50));
        return HttpResponse.json({ ok: true });
      }),
    );

    const controller = new AbortController();
    const pending = client.request(`${BASE}/slow`, { signal: controller.signal });
    controller.abort();

    const result = await pending;

    expect(result).toEqual({ kind: 'err', error: { code: 'aborted', title: 'Request aborted' } });
  });

  it('retries an idempotent GET on a 500 up to the configured cap, then gives up', async () => {
    let calls = 0;
    server.use(
      http.get(`${BASE}/counter`, () => {
        calls += 1;
        return new HttpResponse(null, { status: 500, statusText: 'Internal Server Error' });
      }),
    );

    const result = await client.request(`${BASE}/counter`, {
      retry: { maxAttempts: 3, baseDelayMs: 0, methods: ['GET'] },
    });

    expect(calls).toBe(3);
    expect(result).toEqual({
      kind: 'err',
      error: { code: 'http-error', status: 500, title: 'Internal Server Error' },
    });
  });

  it('never retries a non-idempotent POST', async () => {
    let calls = 0;
    server.use(
      http.post(`${BASE}/mutate`, () => {
        calls += 1;
        return new HttpResponse(null, { status: 500, statusText: 'Internal Server Error' });
      }),
    );

    const result = await client.request(`${BASE}/mutate`, {
      method: 'POST',
      retry: { maxAttempts: 3, baseDelayMs: 0, methods: ['GET'] },
    });

    expect(calls).toBe(1);
    expect(isErr(result)).toBe(true);
  });

  it('sends same-origin credentials on every request', async () => {
    let receivedCredentials: RequestCredentials | undefined;
    server.use(
      http.get(`${BASE}/creds`, ({ request }) => {
        receivedCredentials = (request as unknown as { credentials: RequestCredentials }).credentials;
        return HttpResponse.json({ ok: true });
      }),
    );

    await client.request(`${BASE}/creds`);

    expect(receivedCredentials).toBe('same-origin');
  });

  it('logs a warning through the injected logger on failure', async () => {
    server.use(http.get(`${BASE}/logged`, () => new HttpResponse(null, { status: 404 })));

    const warn = vi.fn();
    await client.request(`${BASE}/logged`, {
      retry: { maxAttempts: 1, baseDelayMs: 0, methods: [] },
      logger: { debug: vi.fn(), info: vi.fn(), warn, error: vi.fn() },
    });

    expect(warn).toHaveBeenCalledWith(
      'http_request_failed',
      expect.objectContaining({
        method: 'GET',
        url: `${BASE}/logged`,
        code: 'http-error',
        status: 404,
      }),
    );
  });
});
