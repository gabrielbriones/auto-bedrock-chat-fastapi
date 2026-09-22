import type { HttpClient } from '@/shared/http/http-client'

import type { SsoGateway } from '@/domains/iam/application/ports'

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
  readonly #navigation: LoginNavigation

  constructor(
    loginUrl: string,
    logoutUrl: string,
    httpClient: SsoHttpClient,
    navigation: LoginNavigation,
  ) {
    this.#loginUrl = loginUrl
    this.#logoutUrl = logoutUrl
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
}