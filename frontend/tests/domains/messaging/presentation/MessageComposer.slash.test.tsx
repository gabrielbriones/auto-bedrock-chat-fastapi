import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, jest } from '@jest/globals'

import { MESSAGING_COPY } from '@/shared/copy/messaging'

import type { SlashCommandOption } from '@/domains/messaging/domain/slash-command'
import { MessageComposer } from '@/domains/messaging/presentation/MessageComposer'

const { composer: COPY } = MESSAGING_COPY

const OPTIONS: readonly SlashCommandOption[] = [
  { name: 'triage', title: 'Job triage', description: 'Triage a failed job.', usage: '/triage job_id=<Job ID>' },
  { name: 'summarize', title: 'Summary', description: '', usage: '/summarize' },
]

const Harness = ({ onSend = jest.fn(), onFocus = jest.fn() }: { onSend?: (text: string) => void; onFocus?: () => void }) => {
  const [draft, setDraft] = useState('')

  return (
    <MessageComposer
      value={draft}
      availability={{ enabled: true, reason: null }}
      awaitingResponse={false}
      onChange={setDraft}
      onSend={onSend}
      commands={{ options: OPTIONS, onFocus }}
    />
  )
}

const field = () => screen.getByRole('textbox', { name: COPY.label })

describe('MessageComposer slash-command menu', () => {
  it('lists the matching skills while a name is typed after a single slash', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(field(), '/')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      expect.stringContaining('/summarize'),
      expect.stringContaining('/triage'),
    ])
    expect(field()).toHaveAttribute('aria-controls', screen.getByRole('listbox').id)

    await user.type(field(), 'tri')
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(screen.getByText(COPY.slashMenu.results(1))).toBeInTheDocument()
  })

  it('completes the highlighted skill with the keyboard instead of sending', async () => {
    const onSend = jest.fn()
    const user = userEvent.setup()
    render(<Harness onSend={onSend} />)

    await user.type(field(), '/')
    await user.keyboard('{ArrowDown}')
    expect(field()).toHaveAttribute('aria-activedescendant', screen.getAllByRole('option')[1]?.id)
    await user.keyboard('{Enter}')

    expect(field()).toHaveValue('/triage ')
    expect(onSend).not.toHaveBeenCalled()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.getByText('/triage job_id=<Job ID>')).toBeInTheDocument()
  })

  it('completes a skill picked with the pointer and keeps focus in the composer', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(field(), '/su')
    await user.click(screen.getByRole('option', { name: /summarize/ }))

    expect(field()).toHaveValue('/summarize ')
    expect(field()).toHaveFocus()
  })

  it('closes on Escape until the draft changes, and Enter then sends', async () => {
    const onSend = jest.fn()
    const user = userEvent.setup()
    render(<Harness onSend={onSend} />)

    await user.type(field(), '/tri')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()

    await user.keyboard('{Enter}')
    expect(onSend).toHaveBeenCalledWith('/tri')
  })

  it('offers nothing for a literal double slash or plain text', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(field(), '//tri')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    await user.clear(field())
    await user.type(field(), 'hello /tri')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('asks for fresh commands on focus', async () => {
    const onFocus = jest.fn()
    const user = userEvent.setup()
    render(<Harness onFocus={onFocus} />)

    await user.click(field())

    expect(onFocus).toHaveBeenCalled()
  })
})
