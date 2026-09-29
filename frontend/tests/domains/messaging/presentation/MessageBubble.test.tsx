import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from '@jest/globals'
import { axe } from 'jest-axe'

import { Instant } from '@/shared/kernel/instant'
import { messageId } from '@/shared/kernel/branded'
import { isOk } from '@/shared/kernel/result'
import type { TranscriptEntry } from '@/domains/messaging/domain/transcript'
import { MessageBubble } from '@/domains/messaging/presentation/MessageBubble'

const atResult = Instant.fromEpochMilliseconds(0)
if (!isOk(atResult)) {
  throw new Error('Expected test instant to be valid')
}
const at = atResult.value

const entry = (overrides: Partial<TranscriptEntry> = {}): TranscriptEntry => ({
  key: 'response',
  message: { id: null, role: 'assistant', raw: '<img src="x" onerror="alert(1)"><thinking>private</thinking>**Answer**', at, seq: 1 },
  pending: false,
  streaming: false,
  transient: false,
  progress: false,
  interrupted: false,
  activity: null,
  ...overrides,
})

describe('MessageBubble', () => {
  it('sanitizes assistant Markdown and removes reasoning blocks', () => {
    const { container } = render(<MessageBubble entry={entry()} />)

    expect(container.querySelector('[onerror]')).toBeNull()
    expect(screen.queryByText('private')).not.toBeInTheDocument()
    expect(screen.getByText('Answer')).toBeInTheDocument()
  })

  it('renders redacted, keyboard-disclosable tool activity with results', async () => {
    const user = userEvent.setup()
    render(
      <MessageBubble
        entry={entry({
          activity: {
            toolCalls: [{ id: 'call-1', name: 'get_jobs', arguments: { api_key: 'secret', keyword: 'retain' } }],
            toolResults: [{ toolCallId: 'call-1', name: 'get_jobs', result: { count: 2, secret: 'hidden' }, error: null }],
            citations: [],
            truncated: false,
          },
        })}
      />,
    )

    const disclosure = screen.getByText('Tool activity: get_jobs')
    await user.click(disclosure)

    expect(screen.getByRole('heading', { name: 'get_jobs' })).toBeInTheDocument()
    expect(screen.getAllByText('[REDACTED]', { exact: false })).toHaveLength(2)
    expect(screen.getByText('retain', { exact: false })).toBeInTheDocument()
    expect(screen.queryByText('hidden', { exact: false })).not.toBeInTheDocument()
  })

  it('keeps citations ordered and shows a truncation notice only when signalled', () => {
    render(
      <MessageBubble
        entry={entry({
          activity: {
            toolCalls: [],
            toolResults: [],
            citations: [
              { documentId: 'first', title: 'First source', source: null, url: 'https://example.com/first', score: 0.9 },
              { documentId: 'second', title: 'Second source', source: null, url: 'https://example.com/second', score: 0.8 },
            ],
            truncated: true,
          },
        })}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Earlier context was truncated')
    expect(screen.getAllByRole('link', { name: /source/ }).map((link) => link.textContent)).toEqual([
      'First source',
      'Second source',
    ])
  })

  // FR-MSG-006a: a half-streamed fence must never reach the Markdown parser, so the swap to
  // sanitised Markdown happens once, at completion.
  it('renders streamed text verbatim and swaps to Markdown exactly once at completion', () => {
    const raw = '**Answer** with an unclosed ```fence'
    const streamed = { id: null, role: 'assistant' as const, raw, at, seq: 1 }
    const view = render(<MessageBubble entry={entry({ message: streamed, streaming: true })} />)

    expect(screen.getByText(raw)).toBeInTheDocument()
    expect(view.container.querySelector('strong')).toBeNull()

    view.rerender(<MessageBubble entry={entry({ message: streamed, streaming: false })} />)

    expect(screen.queryByText(raw)).not.toBeInTheDocument()
    expect(view.container.querySelector('strong')).toHaveTextContent('Answer')
  })

  // FIX-01: preset prompts are Markdown, so a user turn goes through the same pipeline instead of
  // arriving as a wall of pipes and hashes.
  it('renders a user turn as sanitised Markdown, tables included', () => {
    const raw = '#### Configuration\n\n| Parameter | Value |\n|---|---|\n| Frequency | 2.4 |'
    const { container } = render(
      <MessageBubble entry={entry({ message: { id: null, role: 'user', raw, at, seq: 1 } })} />,
    )

    expect(screen.getByRole('heading', { name: 'Configuration' })).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Parameter' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Frequency' })).toBeInTheDocument()
    expect(container.querySelector('.whitespace-pre-wrap')).toBeNull()
  })

  it('keeps a user preset rendered as Markdown while its response streams', () => {
    const raw = '#### Configuration\n\n| Parameter | Value |\n|---|---|\n| Frequency | 2.4 |'
    render(
      <MessageBubble
        entry={entry({ message: { id: null, role: 'user', raw, at, seq: 1 }, pending: true, streaming: true })}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Configuration' })).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('renders no disclosure at all for a turn without tool activity', () => {    render(
      <MessageBubble
        entry={entry({
          activity: { toolCalls: [], toolResults: [], citations: [], truncated: false },
        })}
      />,
    )

    expect(screen.queryByRole('group')).not.toBeInTheDocument()
    expect(screen.queryByText(/Tool activity/)).not.toBeInTheDocument()
  })

  it('renders feedback only for a final assistant message with a server id', () => {
    const feedback = (id: string) => <button type="button">Feedback {id}</button>

    const { rerender } = render(
      <MessageBubble
        entry={entry({ message: { ...entry().message, id: messageId('m-1') } })}
        renderFeedback={feedback}
      />,
    )
    expect(screen.getByRole('button', { name: 'Feedback m-1' })).toBeInTheDocument()

    rerender(
      <MessageBubble
        entry={entry({ message: { ...entry().message, id: messageId('m-2') }, streaming: true, pending: true })}
        renderFeedback={feedback}
      />,
    )
    expect(screen.queryByRole('button', { name: 'Feedback m-2' })).not.toBeInTheDocument()

    rerender(
      <MessageBubble
        entry={entry({ message: { ...entry().message, id: messageId('m-3'), role: 'user' } })}
        renderFeedback={feedback}
      />,
    )
    expect(screen.queryByRole('button', { name: 'Feedback m-3' })).not.toBeInTheDocument()
  })

  // NFR-A11Y-002: the expanded disclosure is the state the axe scan in ChatPanel never reaches.
  it('has no axe violations with the tool disclosure expanded', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <MessageBubble
        entry={entry({
          message: { id: messageId('m-1'), role: 'assistant', raw: '**Answer**', at, seq: 1 },
          activity: {
            toolCalls: [{ id: 'call-1', name: 'get_jobs', arguments: { limit: 2 } }],
            toolResults: [{ toolCallId: 'call-1', name: 'get_jobs', result: { count: 2 }, error: null }],
            citations: [
              { documentId: 'first', title: 'First source', source: null, url: 'https://example.com/first', score: 0.9 },
            ],
            truncated: true,
          },
        })}
      />,
    )

    await user.click(screen.getByText('Tool activity: get_jobs'))

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})