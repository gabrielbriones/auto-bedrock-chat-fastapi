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
  interrupted: null,
  activity: null,
  ...overrides,
})

describe('MessageBubble', () => {
  // XMGPLAT-11977.
  it('shows a turn cut off mid execution as an error notice with no tool activity', () => {
    render(
      <MessageBubble
        entry={entry({
          message: { id: null, role: 'assistant', raw: '', at, seq: 1 },
          interrupted: 'execution',
        })}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent(
      'This request was interrupted in the middle of its execution and did not finish.',
    )
    expect(screen.queryByRole('list', { name: 'Tool calls' })).not.toBeInTheDocument()
  })

  it('sanitizes assistant Markdown and removes reasoning blocks', () => {
    const { container } = render(<MessageBubble entry={entry()} />)

    expect(container.querySelector('[onerror]')).toBeNull()
    expect(screen.queryByText('private')).not.toBeInTheDocument()
    expect(screen.getByText('Answer')).toBeInTheDocument()
  })

  it('lists each tool call with its inline arguments, expandable to redacted input and output', async () => {
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

    const list = screen.getByRole('list', { name: 'Tool calls' })
    expect(list).toHaveTextContent('Ran')
    expect(list).toHaveTextContent('get_jobs')
    // The inline summary is redacted too.
    expect(list).toHaveTextContent('api_key: [REDACTED], keyword: retain')

    await user.click(screen.getByText('get_jobs'))

    expect(screen.getByText('Input')).toBeInTheDocument()
    expect(screen.getByText('Output')).toBeInTheDocument()
    expect(screen.getAllByText('[REDACTED]', { exact: false }).length).toBeGreaterThanOrEqual(3)
    expect(screen.getByText('"retain"')).toBeInTheDocument()
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

  it('marks a failed call and shows its error when expanded', async () => {
    const user = userEvent.setup()
    render(
      <MessageBubble
        entry={entry({
          activity: {
            toolCalls: [{ id: 'call-1', name: 'get_jobs', arguments: {} }],
            toolResults: [{ toolCallId: 'call-1', name: 'get_jobs', result: null, error: 'HTTP 500' }],
            citations: [],
            truncated: false,
          },
        })}
      />,
    )

    expect(screen.getByRole('list', { name: 'Tool calls' })).toHaveTextContent('Failed')
    await user.click(screen.getByText('get_jobs'))
    expect(screen.getByText('Error')).toBeInTheDocument()
    expect(screen.getByText('"HTTP 500"', { exact: false })).toBeInTheDocument()
  })

  it('folds many finished calls under one summary row', () => {
    const toolCalls = Array.from({ length: 6 }, (_, index) => ({ id: `c-${index}`, name: `tool_${index}`, arguments: {} }))
    render(
      <MessageBubble
        entry={entry({
          activity: {
            toolCalls,
            toolResults: [{ toolCallId: 'c-0', name: 'tool_0', result: null, error: 'boom' }],
            citations: [],
            truncated: false,
          },
        })}
      />,
    )

    expect(screen.getByText('Ran 6 tools (1 failed)')).toBeInTheDocument()
  })

  it('shows the in-flight turn\'s tool calls live, with status per call', () => {
    const streamingEntry = (liveTools: NonNullable<TranscriptEntry['liveTools']>) =>
      entry({
        message: { id: null, role: 'assistant', raw: '', at, seq: 1 },
        pending: true,
        liveTools,
      })
    const view = render(
      <MessageBubble
        entry={streamingEntry([
          { id: 'a', name: 'get_jobs', arguments: { status: 'running' }, status: 'succeeded', error: null, durationMs: 1200 },
          { id: 'b', name: 'get_job', arguments: { id: 7 }, status: 'running', error: null, durationMs: null },
        ])}
      />,
    )

    const list = screen.getByRole('list', { name: 'Tool calls' })
    expect(list).toHaveTextContent('Ranget_jobsstatus: running1.2 s')
    expect(list).toHaveTextContent('Runningget_jobid: 7')
    // A running call is its own activity indicator; nothing else is shown underneath.
    expect(screen.queryByText('Thinking...')).not.toBeInTheDocument()

    view.rerender(
      <MessageBubble
        entry={streamingEntry([
          { id: 'a', name: 'get_jobs', arguments: {}, status: 'succeeded', error: null, durationMs: 1200 },
          { id: 'b', name: 'get_job', arguments: {}, status: 'failed', error: 'HTTP 404', durationMs: 30 },
        ])}
      />,
    )

    expect(screen.getByRole('list', { name: 'Tool calls' })).toHaveTextContent('Failedget_job30 ms')
    expect(screen.getByText('Thinking...')).toBeInTheDocument()
  })

  it('streams the next answer text under the live tool calls', () => {
    render(
      <MessageBubble
        entry={entry({
          message: { id: null, role: 'assistant', raw: 'Two jobs are', at, seq: 1 },
          pending: true,
          streaming: true,
          liveTools: [{ id: 'a', name: 'get_jobs', arguments: {}, status: 'succeeded', error: null, durationMs: 5 }],
        })}
      />,
    )

    expect(screen.getByText('Two jobs are')).toBeInTheDocument()
    expect(screen.queryByText('Thinking...')).not.toBeInTheDocument()
  })

  it('renders no tool list at all for a turn without tool activity', () => {
    render(
      <MessageBubble
        entry={entry({
          activity: { toolCalls: [], toolResults: [], citations: [], truncated: false },
        })}
      />,
    )

    expect(screen.queryByRole('group')).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Tool calls' })).not.toBeInTheDocument()
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

    await user.click(screen.getByText('get_jobs'))

    const results = await axe(container)

    expect(results.violations.map((violation) => violation.id)).toEqual([])
  })
})