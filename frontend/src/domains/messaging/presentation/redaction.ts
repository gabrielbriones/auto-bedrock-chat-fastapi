const REDACTED = '[REDACTED]'

const SENSITIVE_KEYS = new Set([
  'api key',
  'access key',
  'authorization',
  'password',
  'secret',
  'token',
])

const normaliseKey = (key: string): string =>
  key
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .toLowerCase()

const isSensitiveKey = (key: string): boolean => SENSITIVE_KEYS.has(normaliseKey(key))

/** Redacts known credential fields without mutating, recursing forever, or matching substrings. */
export const redactSensitive = (value: unknown): unknown => {
  const seen = new WeakMap<object, unknown>()

  const visit = (candidate: unknown): unknown => {
    if (candidate === null || typeof candidate !== 'object') {
      return candidate
    }

    const cached = seen.get(candidate)
    if (cached !== undefined) {
      return cached
    }

    if (Array.isArray(candidate)) {
      const copy: unknown[] = []
      seen.set(candidate, copy)
      copy.push(...candidate.map(visit))
      return copy
    }

    const copy: Record<string, unknown> = {}
    seen.set(candidate, copy)
    for (const [key, nested] of Object.entries(candidate)) {
      copy[key] = isSensitiveKey(key) ? REDACTED : visit(nested)
    }
    return copy
  }

  return visit(value)
}

const REASONING_BLOCK =
  /<(?:analysis|internal[-_]?reasoning|reasoning|scratchpad|think|thinking)\b[^>]*>[\s\S]*?<\/(?:analysis|internal[-_]?reasoning|reasoning|scratchpad|think|thinking)>/gi

/** Removes model-only reasoning tags before assistant Markdown reaches the renderer. */
export const stripReasoningBlocks = (content: string): string => content.replace(REASONING_BLOCK, '')