import { z } from 'zod'

// FR-SHELL-014 / FR-SHELL-014a: URL search state is parsed by the owning route's Zod schema and
// nowhere else. Every parameter below carries its own `.catch(fallback)`, so a hand-edited or
// stale URL degrades one value at a time instead of failing the route; `.default()` keeps the key
// optional for typed navigation while the parsed search always holds a concrete value. Unknown
// keys are dropped by Zod's object strip mode.

// The router's codec JSON-parses values, so `?offset=50` arrives as a number — but a hand-typed
// URL may still deliver a string.
export const offsetParam = z.coerce.number().int().min(0).catch(0).default(0)

// `z.coerce.boolean()` is deliberately avoided: it reads the string "false" as true.
export const booleanParam = (fallback: boolean) =>
  z
    .union([
      z.boolean(),
      z.literal('true').transform(() => true),
      z.literal('false').transform(() => false),
    ])
    .catch(fallback)
    .default(fallback)

// An absent filter and an empty one are the same thing; both drop out of the URL entirely.
export const textParam = z.string().min(1).optional().catch(undefined).optional()
