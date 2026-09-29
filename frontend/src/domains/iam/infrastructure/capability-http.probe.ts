import type { CapabilityProbe } from '@/domains/iam/application/ports'
import { NO_CAPABILITIES, type Capabilities } from '@/domains/iam/domain/capabilities'
import { toCapabilities } from '@/domains/iam/infrastructure/dto/capabilities.dto'
import type { HttpClient } from '@/shared/http/http-client'
import { isErr } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

type CapabilityHttpClient = Pick<HttpClient, 'request'>

export class CapabilityHttpProbe implements CapabilityProbe {
  private cached: Promise<Capabilities> | null = null
  private readonly endpoint: string
  private readonly httpClient: CapabilityHttpClient
  private readonly logger: Logger

  constructor(
    endpoint: string,
    httpClient: CapabilityHttpClient,
    logger: Logger,
  ) {
    this.endpoint = endpoint
    this.httpClient = httpClient
    this.logger = logger
  }

  probe(): Promise<Capabilities> {
    this.cached ??= this.fetchCapabilities()
    return this.cached
  }

  invalidate(): void {
    this.cached = null
  }

  private async fetchCapabilities(): Promise<Capabilities> {
    const result = await this.httpClient.request<unknown>(`${this.endpoint}/_capabilities`, {
      credentials: 'include',
      logger: this.logger,
    })

    if (isErr(result)) {
      return NO_CAPABILITIES
    }

    const capabilities = toCapabilities(result.value)
    if (capabilities === null) {
      this.logger.warn('capability_probe_invalid_response')
      return NO_CAPABILITIES
    }

    return capabilities
  }
}