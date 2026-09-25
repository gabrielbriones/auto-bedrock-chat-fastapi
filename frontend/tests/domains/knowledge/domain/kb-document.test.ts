import { describe, expect, it } from '@jest/globals'

import { kbDocumentId } from '@/shared/kernel/branded'
import { CalendarDate, Instant } from '@/shared/kernel/instant'
import { isOk } from '@/shared/kernel/result'
import {
  createCredibility,
  credibilityBand,
  diffDocument,
  type KbDocument,
  type KbDocumentDraft,
} from '@/domains/knowledge/domain/public'

const calendarDate = (value: string): CalendarDate => {
  const result = CalendarDate.fromIso(value)

  if (!isOk(result)) {
    throw new Error(`invalid test date: ${value}`)
  }

  return result.value
}

const instant = (value: string): Instant => {
  const result = Instant.fromIso(value)

  if (!isOk(result)) {
    throw new Error(`invalid test instant: ${value}`)
  }

  return result.value
}

const originalDocument = (overrides: Partial<KbDocument> = {}): KbDocument => ({
  id: kbDocumentId('kb/2026/perf-guide'),
  title: 'Performance guide',
  source: 'feedback',
  sourceUrl: null,
  topic: 'compute',
  tags: ['ipc', 'vector'],
  content: 'Use the vector path.',
  datePublished: calendarDate('2026-09-01'),
  metadata: { owner: 'perf', nested: { version: 1 } },
  chunkCount: 4,
  createdAt: instant('2026-09-01T12:00:00Z'),
  credibility: createCredibility(0.8, false),
  ...overrides,
})

const draft = (overrides: Partial<KbDocumentDraft> = {}): KbDocumentDraft => ({
  title: 'Performance guide',
  topic: 'compute',
  tags: ['ipc', 'vector'],
  content: 'Use the vector path.',
  datePublished: calendarDate('2026-09-01'),
  metadata: { owner: 'perf', nested: { version: 1 } },
  ...overrides,
})

describe('credibilityBand', () => {
  it.each([
    [0, 'danger'],
    [0.29, 'danger'],
    [0.3, 'warn'],
    [0.69, 'warn'],
    [0.7, 'good'],
    [1, 'good'],
  ] as const)('assigns %s to the %s band', (score, band) => {
    expect(credibilityBand(score, false)).toBe(band)
  })

  it('forces the danger band when removal is flagged', () => {
    expect(credibilityBand(1, true)).toBe('danger')
  })
})

describe('createCredibility', () => {
  it('defaults omitted score and flag values to a healthy document', () => {
    expect(createCredibility(null, null)).toEqual({
      score: 1,
      removalFlagged: false,
      band: 'good',
    })
  })
})

describe('diffDocument', () => {
  it('returns an empty patch when every editable value is unchanged', () => {
    expect(diffDocument(originalDocument(), draft())).toEqual({})
  })

  it('returns only a changed title', () => {
    expect(diffDocument(originalDocument(), draft({ title: 'Updated guide' }))).toEqual({
      title: 'Updated guide',
    })
  })

  it('uses null to clear an empty title or topic', () => {
    expect(diffDocument(originalDocument(), draft({ title: '' }))).toEqual({ title: null })
    expect(diffDocument(originalDocument(), draft({ topic: '' }))).toEqual({ topic: null })
  })

  it('returns only a changed publication date', () => {
    expect(diffDocument(originalDocument(), draft({ datePublished: calendarDate('2026-09-02') }))).toEqual({
      datePublished: calendarDate('2026-09-02'),
    })
  })

  it('treats absent original content and an untouched empty editor as equal', () => {
    expect(
      diffDocument(originalDocument({ content: null }), draft({ content: '' })),
    ).toEqual({})
  })

  it('sends content when its normalised value changes', () => {
    expect(diffDocument(originalDocument(), draft({ content: 'Updated body' }))).toEqual({
      content: 'Updated body',
    })
    expect(diffDocument(originalDocument(), draft({ content: '' }))).toEqual({ content: '' })
  })

  it('ignores tag order but detects additions and removals', () => {
    expect(diffDocument(originalDocument(), draft({ tags: ['vector', 'ipc'] }))).toEqual({})
    expect(diffDocument(originalDocument(), draft({ tags: ['ipc', 'vector', 'cache'] }))).toEqual({
      tags: ['ipc', 'vector', 'cache'],
    })
    expect(diffDocument(originalDocument(), draft({ tags: ['ipc'] }))).toEqual({ tags: ['ipc'] })
    expect(diffDocument(originalDocument(), draft({ tags: [] }))).toEqual({ tags: [] })
  })

  it('compares metadata structurally rather than by object identity or key order', () => {
    expect(
      diffDocument(
        originalDocument(),
        draft({ metadata: { nested: { version: 1 }, owner: 'perf' } }),
      ),
    ).toEqual({})
    expect(diffDocument(originalDocument(), draft({ metadata: { owner: 'docs' } }))).toEqual({
      metadata: { owner: 'docs' },
    })
    expect(diffDocument(originalDocument(), draft({ metadata: {} }))).toEqual({ metadata: {} })
  })

  it('compares nested arrays and handles null dates without false changes', () => {
    const metadata = { links: [{ label: 'guide', weight: 1 }], optional: null }

    expect(
      diffDocument(
        originalDocument({ datePublished: null, metadata }),
        draft({ datePublished: null, metadata: { optional: null, links: [{ label: 'guide', weight: 1 }] } }),
      ),
    ).toEqual({})
    expect(
      diffDocument(
        originalDocument({ metadata }),
        draft({ metadata: { links: [{ label: 'guide', weight: 2 }], optional: null } }),
      ),
    ).toEqual({ metadata: { links: [{ label: 'guide', weight: 2 }], optional: null } })
  })

  it('returns all changed fields without leaking unchanged values', () => {
    expect(
      diffDocument(
        originalDocument(),
        draft({
          title: 'New title',
          topic: 'memory',
          tags: ['new'],
          content: 'New content',
          datePublished: null,
          metadata: {},
        }),
      ),
    ).toEqual({
      title: 'New title',
      topic: 'memory',
      tags: ['new'],
      content: 'New content',
      datePublished: null,
      metadata: {},
    })
  })
})