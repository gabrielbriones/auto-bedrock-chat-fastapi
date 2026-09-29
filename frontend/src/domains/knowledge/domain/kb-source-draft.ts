import { err, ok, unwrapOr, type Result } from '@/shared/kernel/result'

import type { FileIngestRequest, WebCrawlRequest } from '@/domains/knowledge/domain/kb-source'

// The form drafts are strings the way the inputs hold them; validation here turns a draft into a
// request the gateway can send, or names the field-level issues the form has to show. The issue
// codes are data — the copy module owns their wording.

export const MAX_URLS = 20
export const MIN_MAX_PAGES = 1
export const MAX_MAX_PAGES = 10_000

export type WebCrawlDraft = {
  readonly name: string
  readonly urls: string
  readonly topic: string
  readonly maxDepth: string
  readonly maxPages: string
  readonly allowedDomains: string
  readonly excludePatterns: string
  readonly ingestLinkedFiles: boolean
  readonly headers: string
  readonly cookies: string
}

export const EMPTY_WEB_CRAWL_DRAFT: WebCrawlDraft = Object.freeze({
  name: '',
  urls: '',
  topic: '',
  maxDepth: '2',
  maxPages: '100',
  allowedDomains: '',
  excludePatterns: '',
  ingestLinkedFiles: false,
  headers: '',
  cookies: '',
})

export type FileIngestDraft = {
  readonly name: string
  readonly topic: string
  readonly files: readonly File[]
}

export const EMPTY_FILE_INGEST_DRAFT: FileIngestDraft = Object.freeze({ name: '', topic: '', files: [] })

export type KbSourceIssue =
  | 'name-required'
  | 'urls-required'
  | 'too-many-urls'
  | 'invalid-depth'
  | 'invalid-pages'
  | 'invalid-json'
  | 'not-a-string-map'
  | 'files-required'

export type WebCrawlField = 'name' | 'urls' | 'maxDepth' | 'maxPages' | 'headers' | 'cookies'
export type WebCrawlIssues = Partial<Readonly<Record<WebCrawlField, KbSourceIssue>>>

export type FileIngestField = 'name' | 'files'
export type FileIngestIssues = Partial<Readonly<Record<FileIngestField, KbSourceIssue>>>

// A CSV/newline-separated field becomes a trimmed, non-empty list.
export const splitList = (raw: string): readonly string[] =>
  raw
    .split(/[\n,]/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0)

const optionalText = (raw: string): string | null => {
  const trimmed = raw.trim()
  return trimmed.length > 0 ? trimmed : null
}

const optionalList = (raw: string): readonly string[] | null => {
  const parts = splitList(raw)
  return parts.length > 0 ? parts : null
}

// Headers and cookies are sent verbatim as a `{name: value}` map; anything else is refused before
// it reaches the wire rather than surfacing as a server-side validation error.
export const parseStringMap = (
  text: string,
): Result<Readonly<Record<string, string>> | null, 'invalid-json' | 'not-a-string-map'> => {
  if (text.trim().length === 0) {
    return ok(null)
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return err('invalid-json')
  }

  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return err('not-a-string-map')
  }

  const entries = Object.entries(parsed as Record<string, unknown>)
  if (!entries.every(([, value]) => typeof value === 'string')) {
    return err('not-a-string-map')
  }

  return ok(Object.fromEntries(entries) as Record<string, string>)
}

const parseBoundedInteger = (raw: string, min: number, max: number): Result<number | null, 'invalid'> => {
  if (raw.trim().length === 0) {
    return ok(null)
  }

  const value = Number(raw)
  return Number.isInteger(value) && value >= min && value <= max ? ok(value) : err('invalid')
}

export const validateWebCrawlDraft = (draft: WebCrawlDraft): Result<WebCrawlRequest, WebCrawlIssues> => {
  const issues: Partial<Record<WebCrawlField, KbSourceIssue>> = {}
  const name = draft.name.trim()
  const urls = splitList(draft.urls)
  const maxDepth = parseBoundedInteger(draft.maxDepth, 0, Number.MAX_SAFE_INTEGER)
  const maxPages = parseBoundedInteger(draft.maxPages, MIN_MAX_PAGES, MAX_MAX_PAGES)
  const headers = parseStringMap(draft.headers)
  const cookies = parseStringMap(draft.cookies)

  if (name.length === 0) issues.name = 'name-required'
  if (urls.length === 0) issues.urls = 'urls-required'
  else if (urls.length > MAX_URLS) issues.urls = 'too-many-urls'
  if (maxDepth.kind === 'err') issues.maxDepth = 'invalid-depth'
  if (maxPages.kind === 'err') issues.maxPages = 'invalid-pages'
  if (headers.kind === 'err') issues.headers = headers.error
  if (cookies.kind === 'err') issues.cookies = cookies.error

  if (Object.keys(issues).length > 0) {
    return err(issues)
  }

  return ok({
    name,
    urls,
    topic: optionalText(draft.topic),
    maxDepth: unwrapOr(maxDepth, null),
    maxPages: unwrapOr(maxPages, null),
    allowedDomains: optionalList(draft.allowedDomains),
    excludePatterns: optionalList(draft.excludePatterns),
    ingestLinkedFiles: draft.ingestLinkedFiles,
    headers: unwrapOr(headers, null),
    cookies: unwrapOr(cookies, null),
  })
}

export const validateFileIngestDraft = (
  draft: FileIngestDraft,
): Result<FileIngestRequest, FileIngestIssues> => {
  const issues: Partial<Record<FileIngestField, KbSourceIssue>> = {}
  const name = draft.name.trim()

  if (name.length === 0) issues.name = 'name-required'
  if (draft.files.length === 0) issues.files = 'files-required'

  if (Object.keys(issues).length > 0) {
    return err(issues)
  }

  return ok({ name, topic: optionalText(draft.topic), files: draft.files })
}
