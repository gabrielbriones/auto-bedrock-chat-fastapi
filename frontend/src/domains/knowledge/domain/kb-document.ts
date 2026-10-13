import type { KbDocumentId } from '@/shared/kernel/branded'
import type { CalendarDate, Instant } from '@/shared/kernel/instant'

export type CredibilityBand = 'danger' | 'warn' | 'good'

export type Credibility = {
  readonly score: number
  readonly removalFlagged: boolean
  readonly band: CredibilityBand
}

export const credibilityBand = (score: number, removalFlagged: boolean): CredibilityBand => {
  if (removalFlagged || score < 0.3) {
    return 'danger'
  }

  return score >= 0.7 ? 'good' : 'warn'
}

export const createCredibility = (
  score: number | null | undefined,
  removalFlagged: boolean | null | undefined,
): Credibility => {
  const normalizedScore = score ?? 1
  const normalizedFlag = removalFlagged ?? false

  return {
    score: normalizedScore,
    removalFlagged: normalizedFlag,
    band: credibilityBand(normalizedScore, normalizedFlag),
  }
}

export type KbDocument = {
  readonly id: KbDocumentId
  readonly title: string | null
  readonly source: string | null
  readonly sourceUrl: string | null
  readonly topic: string | null
  readonly tags: readonly string[]
  readonly content: string | null
  readonly datePublished: CalendarDate | null
  readonly metadata: Readonly<Record<string, unknown>>
  readonly chunkCount: number | null
  readonly createdAt: Instant | null
  readonly credibility: Credibility
}

export type KbDocumentSummary = Pick<
  KbDocument,
  'id' | 'title' | 'source' | 'sourceUrl' | 'topic' | 'tags' | 'chunkCount' | 'createdAt' | 'credibility'
>

export type KbDocumentDraft = Pick<
  KbDocument,
  'title' | 'topic' | 'tags' | 'content' | 'datePublished' | 'metadata'
>

export type SparsePatch = {
  readonly title?: string | null
  readonly topic?: string | null
  readonly tags?: readonly string[]
  readonly content?: string
  readonly datePublished?: CalendarDate | null
  readonly metadata?: Readonly<Record<string, unknown>>
}

const normaliseText = (value: string | null | undefined): string | null => value || null

const sameTags = (left: readonly string[], right: readonly string[]): boolean => {
  if (left.length !== right.length) {
    return false
  }

  const rightSet = new Set(right)
  return left.every((tag) => rightSet.has(tag))
}

const sameJsonValue = (left: unknown, right: unknown): boolean => {
  if (Object.is(left, right)) {
    return true
  }

  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((value, index) => sameJsonValue(value, right[index]))
  }

  if (typeof left !== 'object' || left === null || typeof right !== 'object' || right === null) {
    return false
  }

  const leftRecord = left as Record<string, unknown>
  const rightRecord = right as Record<string, unknown>
  const leftKeys = Object.keys(leftRecord)
  const rightKeys = Object.keys(rightRecord)

  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every((key) => key in rightRecord && sameJsonValue(leftRecord[key], rightRecord[key]))
  )
}

const sameDate = (left: CalendarDate | null, right: CalendarDate | null): boolean =>
  left === right || (left !== null && right !== null && left.equals(right))

export const diffDocument = (original: KbDocument, draft: KbDocumentDraft): SparsePatch => {
  const title = normaliseText(draft.title)
  const topic = normaliseText(draft.topic)
  const originalContent = original.content ?? ''
  const draftContent = draft.content ?? ''

  return {
    ...(normaliseText(original.title) === title ? {} : { title }),
    ...(normaliseText(original.topic) === topic ? {} : { topic }),
    ...(sameTags(original.tags, draft.tags) ? {} : { tags: [...draft.tags] }),
    ...(originalContent === draftContent ? {} : { content: draftContent }),
    ...(sameDate(original.datePublished, draft.datePublished) ? {} : { datePublished: draft.datePublished }),
    ...(sameJsonValue(original.metadata, draft.metadata) ? {} : { metadata: draft.metadata }),
  }
}

export const toSummary = (document: KbDocument): KbDocumentSummary => ({
  id: document.id,
  title: document.title,
  source: document.source,
  sourceUrl: document.sourceUrl,
  topic: document.topic,
  tags: document.tags,
  chunkCount: document.chunkCount,
  createdAt: document.createdAt,
  credibility: document.credibility,
})