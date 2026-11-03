import { describe, expect, it } from '@jest/globals'

import { replyPreview } from '@/app/notifications/reply-preview'

describe('replyPreview (FR-MSG-032)', () => {
  it('flattens Markdown into one readable line', () => {
    const markdown = [
      '## Summary',
      '',
      '- **CPU** is the bottleneck in [the report](https://example.test/r).',
      '> Consider `perf stat`.',
      '```bash',
      'run --fast',
      '```',
    ].join('\n')

    expect(replyPreview(markdown)).toBe('Summary CPU is the bottleneck in the report. Consider perf stat. run --fast')
  })

  it('keeps underscores inside identifiers', () => {
    expect(replyPreview('Called extract_job_metrics.')).toBe('Called extract_job_metrics.')
  })

  it('drops reasoning blocks and HTML tags', () => {
    expect(replyPreview('<reasoning>hidden</reasoning><b>Visible</b> text')).toBe('Visible text')
  })

  it('truncates long replies with an ellipsis', () => {
    const preview = replyPreview('word '.repeat(100), 20)

    expect(preview).toBe('word word word word…')
  })
})
