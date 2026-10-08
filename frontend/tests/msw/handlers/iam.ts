import { HttpResponse, http } from 'msw'

import { chatBase } from '@/app/bootstrap/loadBootstrap'

const REFRESH = `*${chatBase()}/auth/sso/refresh`

// POST {CHAT}/auth/sso/refresh. A cookie-only caller gets just the new expiry, never the token.
export const iamHandlers = [
  http.post(REFRESH, () => HttpResponse.json({ expires_at: 1_791_388_800 })),
]

export const ssoRefreshUnauthorizedHandler = http.post(REFRESH, () =>
  HttpResponse.json({ error: 'missing_session_token' }, { status: 401 }),
)

export const ssoRefreshIdpFailureHandler = http.post(REFRESH, () =>
  HttpResponse.json({ error: 'refresh_failed' }, { status: 502 }),
)

export const ssoRefreshNetworkErrorHandler = http.post(REFRESH, () =>
  HttpResponse.error(),
)
