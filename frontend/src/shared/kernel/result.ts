export type Ok<T> = {
  readonly kind: 'ok';
  readonly value: T;
};

export type Err<E> = {
  readonly kind: 'err';
  readonly error: E;
};

export type Result<T, E> = Ok<T> | Err<E>;

export const ok = <T>(value: T): Ok<T> => ({ kind: 'ok', value });

export const err = <E>(error: E): Err<E> => ({ kind: 'err', error });

export const isOk = <T, E>(result: Result<T, E>): result is Ok<T> => result.kind === 'ok';

export const isErr = <T, E>(result: Result<T, E>): result is Err<E> => result.kind === 'err';

export const map = <T, E, U>(
  result: Result<T, E>,
  transform: (value: T) => U,
): Result<U, E> => (isOk(result) ? ok(transform(result.value)) : result);

export const mapErr = <T, E, F>(
  result: Result<T, E>,
  transform: (error: E) => F,
): Result<T, F> => (isErr(result) ? err(transform(result.error)) : result);

export const andThen = <T, E, U, F>(
  result: Result<T, E>,
  transform: (value: T) => Result<U, F>,
): Result<U, E | F> => (isOk(result) ? transform(result.value) : result);

export const unwrapOr = <T, E>(result: Result<T, E>, fallback: T): T =>
  isOk(result) ? result.value : fallback;

export const combine = <T, E>(results: readonly Result<T, E>[]): Result<T[], E> => {
  const values: T[] = [];

  for (const result of results) {
    if (isErr(result)) {
      return result;
    }

    values.push(result.value);
  }

  return ok(values);
};