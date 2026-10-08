import type { HttpClient } from '@/shared/http/http-client'

import type { SsoGateway, SsoRefreshOutcome } from '@/domains/iam/application/ports'

type SsoHttpClient = Pick<HttpClient, 'request'>

export interface LoginNavigation {
  assign(url: string): void
}

const appendNext = (loginUrl: string, returnTo: string): string => {
  const separator = loginUrl.includes('?')
    ? loginUrl.endsWith('?') || loginUrl.endsWith('&') ? '' : '&'
    : '?'
  return `${loginUrl}${separator}next=${encodeURIComponent(returnTo)}`
}

export class SsoHttpGateway implements SsoGateway {
  readonly #httpClient: SsoHttpClient
  readonly #loginUrl: string
  readonly #logoutUrl: string
  readonly #refreshUrl: string
  readonly #navigation: LoginNavigation

  constructor(
    loginUrl: string,
    logoutUrl: string,
    refreshUrl: string,
    httpClient: SsoHttpClient,
    navigation: LoginNavigation,
  ) {
    this.#loginUrl = loginUrl
    this.#logoutUrl = logoutUrl
    this.#refreshUrl = refreshUrl
    this.#httpClient = httpClient
    this.#navigation = navigation
  }

  beginLogin(returnTo: string): void {
    this.#navigation.assign(appendNext(this.#loginUrl, returnTo))
  }

  logout() {
    return this.#httpClient.request<void>(this.#logoutUrl, {
      method: 'POST',
      credentials: 'include',
    })
  }

  async refresh(): Promise<SsoRefreshOutcome> {
    const result = await this.#httpClient.request<unknown>(this.#refreshUrl, {
      method: 'POST',
      credentials: 'include',
    })
    if (result.kind === 'ok') {
      return 'renewed'
    }
    // The server answers 401 only when the cookie/session is gone for good.
    return result.error.status === 401 ? 'expired' : 'unavailable'
  }
}