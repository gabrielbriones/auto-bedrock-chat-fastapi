import { describe, expect, it } from 'vitest';

import { abortedProblem, httpErrorProblem, networkErrorProblem, titleForStatus } from '@/shared/http/exception';

describe('titleForStatus', () => {
  it('maps known statuses to a human-readable title', () => {
    expect(titleForStatus(502)).toBe('Bad Gateway');
    expect(titleForStatus(404)).toBe('Not Found');
  });

  it('falls back to statusText, then a generic label, for unknown statuses', () => {
    expect(titleForStatus(599, 'Custom Reason')).toBe('Custom Reason');
    expect(titleForStatus(599)).toBe('HTTP 599');
    expect(titleForStatus(599, '')).toBe('HTTP 599');
  });
});

describe('abortedProblem / networkErrorProblem', () => {
  it('produce distinguishable, stable codes', () => {
    expect(abortedProblem()).toEqual({ code: 'aborted', title: 'Request aborted' });

    const cause = new TypeError('fetch failed');
    expect(networkErrorProblem(cause)).toEqual({
      code: 'network-error',
      title: 'Network error',
      cause,
    });
  });
});

describe('httpErrorProblem', () => {
  it('parses an RFC-7807 JSON body', async () => {
    const response = new Response(
      JSON.stringify({ title: 'Validation failed', detail: 'name is required', type: 'urn:x' }),
      { status: 422, headers: { 'content-type': 'application/problem+json' } },
    );

    await expect(httpErrorProblem(response)).resolves.toEqual({
      code: 'http-error',
      status: 422,
      title: 'Validation failed',
      detail: 'name is required',
      type: 'urn:x',
    });
  });

  it('synthesises a Problem from the status for an HTML error body (FIX-07)', async () => {
    const response = new Response('<html><body>Bad Gateway</body></html>', {
      status: 502,
      statusText: 'Bad Gateway',
      headers: { 'content-type': 'text/html' },
    });

    await expect(httpErrorProblem(response)).resolves.toEqual({
      code: 'http-error',
      status: 502,
      title: 'Bad Gateway',
    });
  });

  it('synthesises a Problem for an empty body', async () => {
    const response = new Response(null, { status: 503, statusText: 'Service Unavailable' });

    await expect(httpErrorProblem(response)).resolves.toEqual({
      code: 'http-error',
      status: 503,
      title: 'Service Unavailable',
    });
  });

  it('synthesises a Problem for malformed JSON rather than throwing a parse error', async () => {
    const response = new Response('{not valid json', {
      status: 500,
      headers: { 'content-type': 'application/json' },
    });

    await expect(httpErrorProblem(response)).resolves.toEqual({
      code: 'http-error',
      status: 500,
      title: 'Internal Server Error',
    });
  });

  it('synthesises a Problem when the JSON body parses to a non-record (e.g. an array)', async () => {
    const response = new Response('[1,2,3]', {
      status: 500,
      headers: { 'content-type': 'application/json' },
    });

    await expect(httpErrorProblem(response)).resolves.toEqual({
      code: 'http-error',
      status: 500,
      title: 'Internal Server Error',
    });
  });

  it('prefers a numeric status field from the body and falls back to titleForStatus when title is not a string', async () => {
    const response = new Response(JSON.stringify({ status: 400, title: 123 }), {
      status: 500,
      headers: { 'content-type': 'application/json' },
    });

    await expect(httpErrorProblem(response)).resolves.toEqual({
      code: 'http-error',
      status: 400,
      title: 'Bad Request',
    });
  });

  it('preserves a backend machine-readable code separately from the transport code', async () => {
    const response = new Response(
      JSON.stringify({ code: 'invalid_date_range', detail: 'end must be after start' }),
      { status: 400, headers: { 'content-type': 'application/json' } },
    );

    await expect(httpErrorProblem(response)).resolves.toMatchObject({
      code: 'http-error',
      serverCode: 'invalid_date_range',
      detail: 'end must be after start',
    });
  });
});
