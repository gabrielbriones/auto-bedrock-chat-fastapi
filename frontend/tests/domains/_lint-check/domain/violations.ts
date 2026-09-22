// Not application code. This module exists so `tests/lint/boundaries.test.ts` has something for the
// layer and context rules to fire on: a rule that quietly matches nothing looks exactly like a
// clean codebase, and that is how the `checkAllOrigins`/alias bug in XMGPLAT-11338 went unnoticed.
// `npm run lint` ignores this path; the test lints it deliberately.
import { useState } from 'react'

// FR-TOOL-011: `iam`'s internals are private to it.
import { toCredentialKind } from '@/domains/iam/domain/credential'

// The legitimate counterpart — the one module of another context that may be imported.
import { CREDENTIAL_KINDS } from '@/domains/iam/domain/public'

export const usesFramework = useState
export const reachesIntoAnotherContext = toCredentialKind
export const readsAPublicApi = CREDENTIAL_KINDS
