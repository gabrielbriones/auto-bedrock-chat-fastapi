export type ReconnectPolicy = {
  readonly baseDelayMs: number;
  readonly maxDelayMs: number;
  readonly maxAttempts: number;
  readonly minJitterMultiplier: number;
  readonly maxJitterMultiplier: number;
};

export const DEFAULT_RECONNECT_POLICY: ReconnectPolicy = {
  baseDelayMs: 1_000,
  maxDelayMs: 30_000,
  maxAttempts: 10,
  minJitterMultiplier: 0.8,
  maxJitterMultiplier: 1.2,
};

export const reconnectDelayMs = (
  attempt: number,
  random: number,
  policy: ReconnectPolicy = DEFAULT_RECONNECT_POLICY,
): number | undefined => {
  if (
    !Number.isInteger(attempt) ||
    attempt < 1 ||
    attempt > policy.maxAttempts ||
    !Number.isFinite(random) ||
    random < 0 ||
    random > 1
  ) {
    return undefined;
  }

  const exponentialDelay = policy.baseDelayMs * 2 ** (attempt - 1);
  const jitterMultiplier =
    policy.minJitterMultiplier +
    random * (policy.maxJitterMultiplier - policy.minJitterMultiplier);

  return Math.min(exponentialDelay * jitterMultiplier, policy.maxDelayMs);
};