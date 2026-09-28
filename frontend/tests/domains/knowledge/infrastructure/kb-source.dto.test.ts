import { describe, expect, it } from '@jest/globals'

import { isErr, isOk } from '@/shared/kernel/result'
import {
  fromWebCrawlRequest,
  toFileIngestFormData,
  toKbSourceDeletion,
  toKbSourceRun,
  toKbSourceSummaries,
} from '@/domains/knowledge/infrastructure/dto/kb-source.dto'
import type { FileIngestRequest, WebCrawlRequest } from '@/domains/knowledge/domain/public'

const request: WebCrawlRequest = {
  name: 'intel-docs',
  urls: ['https://example.com'],
  topic: 'compute',
  maxDepth: 3,
  maxPages: 50,
  allowedDomains: ['example.com'],
  excludePatterns: ['/de/'],
  ingestLinkedFiles: true,
  headers: { Authorization: 'Bearer x' },
  cookies: { session_id: 's' },
}

describe('KB source run DTO', () => {
  it('defaults an empty status payload to an idle run', () => {
    const result = toKbSourceRun({})

    expect(isOk(result) && result.value).toEqual({
      runId: null,
      phase: 'idle',
      sourceName: null,
      sourceType: null,
      startedAt: null,
      finishedAt: null,
      pagesCrawled: 0,
      pagesProcessed: 0,
      filesProcessed: 0,
      chunksWritten: 0,
      error: null,
      errors: [],
    })
  })

  it('maps a full status payload', () => {
    const result = toKbSourceRun({
      run_id: 'run-1',
      phase: 'completed',
      source_name: 'notes',
      source_type: 'file',
      started_at: '2026-09-27T09:00:00Z',
      finished_at: '2026-09-27T09:01:00Z',
      files_processed: 2,
      chunks_written: 9,
      errors: ['a.txt: not UTF-8'],
    })

    expect(isOk(result) && result.value).toMatchObject({
      runId: 'run-1',
      phase: 'completed',
      sourceName: 'notes',
      sourceType: 'file',
      filesProcessed: 2,
      chunksWritten: 9,
      errors: ['a.txt: not UTF-8'],
    })
    expect(isOk(result) && result.value.startedAt?.toIso()).toBe('2026-09-27T09:00:00.000Z')
  })

  it.each([
    [{ phase: 'paused' }],
    [{ started_at: 'yesterday' }],
    [{ chunks_written: -1 }],
  ])('rejects %j as an invalid response', (payload) => {
    const result = toKbSourceRun(payload)

    expect(isErr(result) && result.error.code).toBe('invalid-response')
  })
})

describe('KB source list and deletion DTOs', () => {
  it('maps the summary list and rejects malformed rows', () => {
    expect(isOk(toKbSourceSummaries([{ source: 'feedback', count: 4 }]))).toBe(true)
    expect(isErr(toKbSourceSummaries([{ source: 'feedback' }]))).toBe(true)
    expect(isErr(toKbSourceSummaries({ items: [] }))).toBe(true)
  })

  it('maps a deletion result, with the count optional', () => {
    expect(toKbSourceDeletion({ source: 'feedback', deleted: 4 })).toEqual({ kind: 'ok', value: { source: 'feedback', deleted: 4 } })
    expect(toKbSourceDeletion({ source: 'feedback' })).toEqual({ kind: 'ok', value: { source: 'feedback', deleted: null } })
    expect(isErr(toKbSourceDeletion({ deleted: 4 }))).toBe(true)
  })
})

describe('web crawl body serialisation', () => {
  it('carries the name only for POST and snake-cases every supplied field', () => {
    expect(fromWebCrawlRequest(request, true)).toEqual({
      name: 'intel-docs',
      urls: ['https://example.com'],
      topic: 'compute',
      max_depth: 3,
      max_pages: 50,
      allowed_domains: ['example.com'],
      exclude_patterns: ['/de/'],
      ingest_linked_files: true,
      headers: { Authorization: 'Bearer x' },
      cookies: { session_id: 's' },
    })
    expect(fromWebCrawlRequest(request, false)).not.toHaveProperty('name')
  })

  it('omits absent optionals so server defaults apply, including an unchecked linked-files flag', () => {
    const minimal: WebCrawlRequest = {
      ...request,
      topic: null,
      maxDepth: null,
      maxPages: null,
      allowedDomains: null,
      excludePatterns: null,
      ingestLinkedFiles: false,
      headers: null,
      cookies: null,
    }

    expect(fromWebCrawlRequest(minimal, false)).toEqual({ urls: ['https://example.com'] })
  })
})

describe('file ingestion form data', () => {
  const files = [new File(['a'], 'a.txt', { type: 'text/plain' }), new File(['%PDF'], 'b.pdf', { type: 'application/pdf' })]
  const ingest: FileIngestRequest = { name: 'notes', topic: 'ops', files }

  it('includes name and topic for POST and every file under `files`', () => {
    const formData = toFileIngestFormData(ingest, true)

    expect(formData.get('name')).toBe('notes')
    expect(formData.get('topic')).toBe('ops')
    expect(formData.getAll('files').map((entry) => (entry as File).name)).toEqual(['a.txt', 'b.pdf'])
  })

  it('leaves name out for PUT and topic out when absent', () => {
    const formData = toFileIngestFormData({ ...ingest, topic: null }, false)

    expect(formData.has('name')).toBe(false)
    expect(formData.has('topic')).toBe(false)
    expect(formData.getAll('files')).toHaveLength(2)
  })
})
