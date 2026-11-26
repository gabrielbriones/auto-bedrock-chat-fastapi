import type { HttpClient } from '@/shared/http/http-client'
import type { Problem } from '@/shared/http/exception'
import type { HttpMethod } from '@/shared/http/retry-policy'
import { andThen, err, isErr, map, mapErr, type Result } from '@/shared/kernel/result'
import type { Logger } from '@/shared/logging/logger'

import type { SkillList, SkillsGateway, SkillWriteError } from '@/domains/skills/application/ports'
import type { Skill, SkillContentPatch, SkillCreateRequest, SkillRevisionSummary } from '@/domains/skills/domain/public'
import {
  fromContentPatch,
  fromCreateRequest,
  toSkill,
  toSkillList,
  toSkillRevisions,
} from '@/domains/skills/infrastructure/dto/skill.dto'

type SkillsHttpClient = Pick<HttpClient, 'request'>

const JSON_HEADERS = { 'content-type': 'application/json' } as const
const REVISION_CONFLICT = 'revision_conflict'

const asProblem = (problem: Problem): SkillWriteError => ({ kind: 'problem', problem })

// admin-api.md "Skill management". Skill names match `^[a-z0-9][a-z0-9_-]*$`, so they are path-safe.
export class HttpSkillsGateway implements SkillsGateway {
  private readonly adminPrefix: string
  private readonly httpClient: SkillsHttpClient
  private readonly logger: Logger

  constructor(adminPrefix: string, httpClient: SkillsHttpClient, logger: Logger) {
    this.adminPrefix = adminPrefix
    this.httpClient = httpClient
    this.logger = logger
  }

  async list(signal: AbortSignal): Promise<Result<SkillList, Problem>> {
    return andThen(await this.send('GET', '/skills', undefined, signal), toSkillList)
  }

  async get(name: string, signal: AbortSignal): Promise<Result<Skill, Problem>> {
    return andThen(await this.send('GET', this.path(name), undefined, signal), toSkill)
  }

  async create(request: SkillCreateRequest): Promise<Result<Skill, Problem>> {
    return andThen(await this.send('POST', '/skills', fromCreateRequest(request)), toSkill)
  }

  patch(name: string, expectedRevision: number, changes: SkillContentPatch): Promise<Result<Skill, SkillWriteError>> {
    return this.writeContent(name, 'PATCH', this.path(name), fromContentPatch(expectedRevision, changes))
  }

  rollback(name: string, revision: number, expectedRevision: number): Promise<Result<Skill, SkillWriteError>> {
    return this.writeContent(name, 'POST', this.path(name, '/rollback'), {
      revision,
      expected_revision: expectedRevision,
    })
  }

  async setEnabled(name: string, enabled: boolean): Promise<Result<Skill, Problem>> {
    return andThen(await this.send('POST', this.path(name, enabled ? '/enable' : '/disable')), toSkill)
  }

  async remove(name: string): Promise<Result<void, Problem>> {
    return map(await this.send('DELETE', this.path(name)), () => undefined)
  }

  async listRevisions(name: string, signal: AbortSignal): Promise<Result<readonly SkillRevisionSummary[], Problem>> {
    return andThen(await this.send('GET', this.path(name, '/revisions'), undefined, signal), toSkillRevisions)
  }

  // The transport keeps only the error envelope, so the stored skill a `409 revision_conflict`
  // carries under `current` is read back with a GET.
  private async writeContent(
    name: string,
    method: HttpMethod,
    path: string,
    body: unknown,
  ): Promise<Result<Skill, SkillWriteError>> {
    const response = await this.send(method, path, body)
    if (isErr(response) && response.error.status === 409 && response.error.serverCode === REVISION_CONFLICT) {
      const current = await this.get(name, AbortSignal.timeout(30_000))
      return isErr(current)
        ? err(asProblem(response.error))
        : err({ kind: 'conflict', current: current.value })
    }
    return mapErr(andThen(response, toSkill), asProblem)
  }

  private path(name: string, suffix = ''): string {
    return `/skills/${encodeURIComponent(name)}${suffix}`
  }

  private send(method: HttpMethod, path: string, body?: unknown, signal?: AbortSignal): Promise<Result<unknown, Problem>> {
    return this.httpClient.request<unknown>(`${this.adminPrefix}${path}`, {
      method,
      ...(body === undefined ? {} : { headers: JSON_HEADERS, body: JSON.stringify(body) }),
      ...(signal === undefined ? {} : { signal }),
      credentials: 'include',
      logger: this.logger,
    })
  }
}
