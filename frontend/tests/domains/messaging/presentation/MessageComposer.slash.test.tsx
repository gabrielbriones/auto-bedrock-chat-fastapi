import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, jest } from '@jest/globals'

import { MESSAGING_COPY } from '@/shared/copy/messaging'

import type { SlashCommandOption, SlashCommandParameter } from '@/domains/messaging/domain/slash-command'
import { MessageComposer } from '@/domains/messaging/presentation/MessageComposer'

const { composer: COPY } = MESSAGING_COPY

const parameter = (name: string, extra: Partial<SlashCommandParameter> = {}): SlashCommandParameter => ({
  name,
  label: name,
  required: false,
  inputType: 'text',
  options: [],
  placeholder: '',
  defaultValue: null,
  ...extra,
})

const OPTIONS: readonly SlashCommandOption[] = [
  {
    name: 'triage',
    title: 'Job triage',
    description: 'Triage a failed job.',
    usage: '/triage job_id=<Job ID> [format=md|txt]',
    parameters: [
      parameter('job_id', { label: 'Job ID', required: true, placeholder: 'e.g. j-123' }),
      parameter('format', { label: 'Format', inputType: 'select', options: ['md', 'txt'] }),
    ],
  },
  { name: 'summarize', title: 'Summary', description: '', usage: '/summarize', parameters: [] },
  {
    name: 'ask',
    title: 'Ask',
    description: '',
    usage: '/ask [input=<Question>]',
    parameters: [parameter('input', { label: 'Question', required: true })],
  },
]

const Harness = ({
  onSend = jest.fn(),
  onFocus = jest.fn(),
  options = OPTIONS,
  unavailable = false,
}: {
  onSend?: (text: string) => void
  onFocus?: () => void
  options?: readonly SlashCommandOption[]
  unavailable?: boolean
}) => {
  const [draft, setDraft] = useState('')

  return (
    <MessageComposer
      value={draft}
      availability={{ enabled: true, reason: null }}
      awaitingResponse={false}
      onChange={setDraft}
      onSend={(text) => {
        setDraft('')
        onSend(text)
      }}
      commands={{ options, onFocus, unavailable }}
    />
  )
}

// By label: a hidden textarea has no accessible name to query by role.
const field = () => screen.getByLabelText(COPY.label, { selector: 'textarea' })
const send = () => screen.getByRole('button', { name: COPY.send })

const setup = (props: Parameters<typeof Harness>[0] = {}) => {
  const user = userEvent.setup()
  render(<Harness {...props} />)
  return user
}

describe('MessageComposer slash commands', () => {
  it('lists the matching skills while a name is typed after a single slash', async () => {
    const user = setup()

    await user.type(field(), '/')
    expect(screen.getAllByRole('option')).toHaveLength(3)
    expect(field()).toHaveAttribute('aria-controls', screen.getByRole('listbox').id)

    await user.type(field(), 'tri')
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(screen.getByText(COPY.slashMenu.results(1))).toBeInTheDocument()
  })

  it('turns a chosen skill into fields and sends the command built from them', async () => {
    const onSend = jest.fn()
    const user = setup({ onSend })

    await user.type(field(), '/tri')
    await user.keyboard('{Enter}')

    const jobId = screen.getByRole('textbox', { name: /Job ID/ })
    expect(screen.getByRole('group', { name: COPY.slashMenu.parameters('triage') })).toBeInTheDocument()
    expect(jobId).toHaveFocus()
    expect(jobId).toHaveAttribute('placeholder', 'e.g. j-123')
    expect(field()).not.toBeVisible()
    expect(send()).toBeDisabled()

    await user.type(jobId, 'job 42')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Format' }), 'txt')
    await user.type(jobId, '{Enter}')

    expect(onSend).toHaveBeenCalledWith('/triage job_id="job 42" format=txt')
    expect(screen.queryByRole('group')).not.toBeInTheDocument()
    expect(field()).toBeVisible()
  })

  it('opens the fields for a command typed in full', async () => {
    const user = setup()

    await user.type(field(), '/triage ')

    expect(screen.getByRole('textbox', { name: /Job ID/ })).toBeInTheDocument()
  })

  it('drops the skill on Escape or its remove button and returns to the message', async () => {
    const user = setup()

    await user.type(field(), '/tri{Enter}')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('group')).not.toBeInTheDocument()

    await user.type(field(), '/tri{Enter}')
    await user.click(screen.getByRole('button', { name: COPY.slashMenu.cancel }))
    expect(screen.queryByRole('group')).not.toBeInTheDocument()
    expect(field()).toHaveValue('')
  })

  it('uses the message box for a skill that takes free text', async () => {
    const onSend = jest.fn()
    const user = setup({ onSend })

    await user.type(field(), '/ask{Enter}')
    expect(field()).toHaveAttribute('placeholder', 'Question')
    expect(send()).toBeDisabled()

    await user.type(field(), 'why did it fail?{Enter}')

    expect(onSend).toHaveBeenCalledWith('/ask input="why did it fail?"')
  })

  it('drops the skill on Backspace in an empty message box', async () => {
    const user = setup()

    await user.type(field(), '/ask{Enter}')
    await user.keyboard('{Backspace}')

    expect(screen.queryByRole('group')).not.toBeInTheDocument()
  })

  it('runs a skill without parameters on Enter', async () => {
    const onSend = jest.fn()
    const user = setup({ onSend })

    await user.type(field(), '/sum{Enter}')
    expect(field()).toHaveAttribute('placeholder', COPY.slashMenu.run('summarize'))
    await user.keyboard('{Enter}')

    expect(onSend).toHaveBeenCalledWith('/summarize')
  })

  it('picks a skill with the pointer', async () => {
    const user = setup()

    await user.type(field(), '/su')
    await user.click(screen.getByRole('option', { name: /summarize/ }))

    expect(screen.getByRole('group', { name: COPY.slashMenu.parameters('summarize') })).toBeInTheDocument()
  })

  it('closes the menu on Escape until the draft changes, and Enter then sends the text', async () => {
    const onSend = jest.fn()
    const user = setup({ onSend })

    await user.type(field(), '/tri')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    await user.keyboard('{Enter}')

    expect(onSend).toHaveBeenCalledWith('/tri')
  })

  it('offers nothing for a literal double slash or plain text', async () => {
    const user = setup()

    await user.type(field(), '//tri')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    await user.clear(field())
    await user.type(field(), 'hello /tri')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('asks for fresh commands on focus', async () => {
    const onFocus = jest.fn()
    const user = setup({ onFocus })

    await user.click(field())

    expect(onFocus).toHaveBeenCalled()
  })

  it('says the skills could not be loaded and still sends the typed command', async () => {
    const onSend = jest.fn()
    const user = setup({ onSend, options: [], unavailable: true })

    await user.type(field(), '/triage')
    expect(screen.getByText(COPY.slashMenu.unavailable)).toBeInTheDocument()

    await user.type(field(), ' job_id=1{Enter}')

    expect(onSend).toHaveBeenCalledWith('/triage job_id=1')
    expect(screen.queryByText(COPY.slashMenu.unavailable)).not.toBeInTheDocument()
  })
})
