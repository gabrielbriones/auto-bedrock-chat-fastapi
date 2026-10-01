import { describe, expect, it } from '@jest/globals'

import { isErr, isOk } from '@/shared/kernel/result'
import {
  EMPTY_FILE_INGEST_DRAFT,
  EMPTY_WEB_CRAWL_DRAFT,
  IDLE_RUN,
  isRunActive,
  isRunTerminal,
  parseStringMap,
  processedCount,
  splitList,
  validateFileIngestDraft,
  validateWebCrawlDraft,
  type KbSourceRun,
  type WebCrawlDraft,
} from '@/domains/knowledge/domain/public'

const draft = (overrides: Partial<WebCrawlDraft> = {}): WebCrawlDraft => ({
  ...EMPTY_WEB_CRAWL_DRAFT,
  name: ' intel-docs ',
  urls: 'https://a.example\nhttps://b.example, https://c.example\n\n',
  ...overrides,
})

const run = (overrides: Partial<KbSourceRun> = {}): KbSourceRun => ({ ...IDLE_RUN, ...overrides })

describe('splitList', () => {
  it('splits on commas and newlines, trims, and drops blanks', () => {
    expect(splitList(' a, b\n\nc ,\n')).toEqual(['a', 'b', 'c'])
    expect(splitList('')).toEqual([])
  })
})

describe('parseStringMap', () => {
  it('reads a blank field as absent', () => {
    expect(parseStringMap('  \n')).toEqual({ kind: 'ok', value: null })
  })

  it('accepts a flat string map', () => {
    expect(parseStringMap('{"Authorization": "Bearer x"}')).toEqual({
      kind: 'ok',
      value: { Authorization: 'Bearer x' },
    })
  })

  it.each([
    ['{not json', 'invalid-json'],
    ['[1, 2]', 'not-a-string-map'],
    ['"text"', 'not-a-string-map'],
    ['null', 'not-a-string-map'],
    ['{"retries": 3}', 'not-a-string-map'],
  ])('rejects %s as %s', (text, issue) => {
    expect(parseStringMap(text)).toEqual({ kind: 'err', error: issue })
  })
})

describe('validateWebCrawlDraft', () => {
  it('names every missing required field at once', () => {
    const result = validateWebCrawlDraft(EMPTY_WEB_CRAWL_DRAFT)

    expect(isErr(result) && result.error).toEqual({ name: 'name-required', urls: 'urls-required' })
  })

  it('caps the start URLs at the server limit', () => {
    const urls = Array.from({ length: 21 }, (_, index) => `https://example.com/${index}`).join('\n')

    const result = validateWebCrawlDraft(draft({ urls }))

    expect(isErr(result) && result.error).toEqual({ urls: 'too-many-urls' })
  })

  it.each(['-1', '1.5', 'two'])('rejects max depth %s', (maxDepth) => {
    const result = validateWebCrawlDraft(draft({ maxDepth }))

    expect(isErr(result) && result.error).toEqual({ maxDepth: 'invalid-depth' })
  })

  it.each(['0', '10001', 'many'])('rejects max pages %s', (maxPages) => {
    const result = validateWebCrawlDraft(draft({ maxPages }))

    expect(isErr(result) && result.error).toEqual({ maxPages: 'invalid-pages' })
  })

  it('reports header and cookie JSON problems on their own fields', () => {
    const result = validateWebCrawlDraft(draft({ headers: '{', cookies: '["a"]' }))

    expect(isErr(result) && result.error).toEqual({ headers: 'invalid-json', cookies: 'not-a-string-map' })
  })

  it('builds a request with omitted optionals as null and lists split', () => {
    const result = validateWebCrawlDraft(draft({ maxDepth: '', maxPages: '', topic: '  ' }))

    expect(isOk(result) && result.value).toEqual({
      name: 'intel-docs',
      urls: ['https://a.example', 'https://b.example', 'https://c.example'],
      topic: null,
      maxDepth: null,
      maxPages: null,
      allowedDomains: null,
      excludePatterns: null,
      ingestLinkedFiles: false,
      synthesize: false,
      headers: null,
      cookies: null,
    })
  })

  it('carries every supplied optional through', () => {
    const result = validateWebCrawlDraft(
      draft({
        topic: 'compute',
        maxDepth: '0',
        maxPages: '10000',
        allowedDomains: 'a.example, b.example',
        excludePatterns: '/de/\n/es/',
        ingestLinkedFiles: true,
        synthesize: true,
        headers: '{"Authorization": "Bearer x"}',
        cookies: '{"session_id": "s"}',
      }),
    )

    expect(isOk(result) && result.value).toMatchObject({
      topic: 'compute',
      maxDepth: 0,
      maxPages: 10_000,
      allowedDomains: ['a.example', 'b.example'],
      excludePatterns: ['/de/', '/es/'],
      ingestLinkedFiles: true,
      synthesize: true,
      headers: { Authorization: 'Bearer x' },
      cookies: { session_id: 's' },
    })
  })
})

describe('validateFileIngestDraft', () => {
  const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })

  it('requires a name and at least one file', () => {
    const result = validateFileIngestDraft(EMPTY_FILE_INGEST_DRAFT)

    expect(isErr(result) && result.error).toEqual({ name: 'name-required', files: 'files-required' })
  })

  it('trims the name and drops a blank topic', () => {
    const result = validateFileIngestDraft({ name: ' notes ', topic: ' ', files: [file], synthesize: false })

    expect(isOk(result) && result.value).toEqual({ name: 'notes', topic: null, files: [file], synthesize: false })
  })

  it('keeps a supplied topic and the synthesize opt-in', () => {
    const result = validateFileIngestDraft({ name: 'notes', topic: 'ops', files: [file], synthesize: true })

    expect(isOk(result) && result.value).toMatchObject({ topic: 'ops', synthesize: true })
  })
})

describe('run helpers', () => {
  it('distinguishes active from terminal phases', () => {
    expect(isRunActive(run({ phase: 'running' }))).toBe(true)
    expect(isRunActive(IDLE_RUN)).toBe(false)
    expect(isRunTerminal(run({ phase: 'completed' }))).toBe(true)
    expect(isRunTerminal(run({ phase: 'failed' }))).toBe(true)
    expect(isRunTerminal(run({ phase: 'running' }))).toBe(false)
  })

  it('counts files for a file run and pages otherwise', () => {
    expect(processedCount(run({ sourceType: 'file', filesProcessed: 3, pagesProcessed: 9 }))).toBe(3)
    expect(processedCount(run({ sourceType: 'web', filesProcessed: 3, pagesProcessed: 9 }))).toBe(9)
    expect(processedCount(run({ pagesProcessed: 2 }))).toBe(2)
  })
})
