// Transport error shape (FIX-07, NFR-OBS-002): HTML/non-JSON error bodies and transport
// failures all normalise here instead of surfacing a raw JSON-parse exception to callers.
// `invalid-response` is the contract-drift case: the request succeeded, the payload did not match
// the schema the anti-corruption layer expects (CT-2).
export type ProblemCode = 'http-error' | 'network-error' | 'aborted' | 'invalid-response';

export type Problem = {
  readonly code: ProblemCode;
  readonly title: string;
  readonly status?: number;
  readonly serverCode?: string;
  readonly detail?: string;
  readonly type?: string;
  readonly issues?: readonly string[];
  readonly cause?: unknown;
};

const STATUS_TITLES: Readonly<Record<number, string>> = {
  400: 'Bad Request',
  401: 'Unauthorized',
  403: 'Forbidden',
  404: 'Not Found',
  409: 'Conflict',
  422: 'Unprocessable Entity',
  429: 'Too Many Requests',
  500: 'Internal Server Error',
  502: 'Bad Gateway',
  503: 'Service Unavailable',
  504: 'Gateway Timeout',
};

export const titleForStatus = (status: number, fallback?: string): string => {
  const known = STATUS_TITLES[status];
  if (known !== undefined) {
    return known;
  }

  return fallback !== undefined && fallback.length > 0 ? fallback : `HTTP ${status}`;
};

export const abortedProblem = (): Problem => ({
  code: 'aborted',
  title: 'Request aborted',
});

export const networkErrorProblem = (cause: unknown): Problem => ({
  code: 'network-error',
  title: 'Network error',
  cause,
});

// Raised by an anti-corruption mapper, never by the transport: a 200 whose body the schema
// rejects is a contract failure and has to be distinguishable from a server error.
export const invalidResponseProblem = (title: string, issues: readonly string[]): Problem => ({
  code: 'invalid-response',
  title,
  issues,
});

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null;

const readString = (record: Record<string, unknown>, key: string): string | undefined => {
  const value = record[key];
  return typeof value === 'string' ? value : undefined;
};

const readNumber = (record: Record<string, unknown>, key: string): number | undefined => {
  const value = record[key];
  return typeof value === 'number' ? value : undefined;
};

const readOptionalString = (record: Record<string, unknown>, key: string): string | undefined => {
  const value = record[key];
  return typeof value === 'string' && value.length > 0 ? value : undefined;
};

const problemFromBody = (body: Record<string, unknown>, fallbackStatus: number): Problem => {
  const title = readString(body, 'title');
  const detail = readString(body, 'detail');
  const type = readString(body, 'type');
  const serverCode = readOptionalString(body, 'code');
  const status = readNumber(body, 'status') ?? fallbackStatus;

  return {
    code: 'http-error',
    status,
    title: title ?? titleForStatus(status),
    ...(serverCode !== undefined ? { serverCode } : {}),
    ...(detail !== undefined ? { detail } : {}),
    ...(type !== undefined ? { type } : {}),
  };
};

// Builds a Problem from a non-OK Response: parses an RFC-7807 body when present, otherwise
// synthesises one from the status so an empty/HTML/malformed body never throws a parse error.
export const httpErrorProblem = async (response: Response): Promise<Problem> => {
  const contentType = response.headers.get('content-type') ?? '';

  if (contentType.includes('json')) {
    try {
      const body: unknown = await response.json();
      if (isRecord(body)) {
        return problemFromBody(body, response.status);
      }
    } catch {
      // Malformed JSON body; fall through to the synthesized problem below.
    }
  }

  return {
    code: 'http-error',
    status: response.status,
    title: titleForStatus(response.status, response.statusText),
  };
};
