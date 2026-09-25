import { useState } from 'react'
import { describe, expect, it } from '@jest/globals'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { TagChipInput } from '@/components/ui/composed/tag-chip-input'
import { REVIEW_COPY } from '@/shared/copy/review'

function Harness({ initial = [] }: { readonly initial?: readonly string[] }) {
  const [tags, setTags] = useState(initial)

  return <TagChipInput id="tags" label="Reviewer tags" tags={tags} onChange={setTags} />
}

const input = () => screen.getByRole('textbox', { name: 'Reviewer tags' })

describe('TagChipInput', () => {
  it('commits on Enter', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(input(), 'emon{Enter}')

    expect(screen.getByText('emon')).toBeInTheDocument()
    expect(input()).toHaveValue('')
  })

  it('commits on comma', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(input(), 'ipc,')

    expect(screen.getByText('ipc')).toBeInTheDocument()
  })

  it('does not commit a partial tag on blur', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(input(), 'unfinished')
    await user.tab()

    expect(screen.queryByText('unfinished')).not.toBeInTheDocument()
    expect(input()).toHaveValue('unfinished')
  })

  it('removes the last tag with Backspace only when the draft is empty', async () => {
    const user = userEvent.setup()
    render(<Harness initial={['emon', 'ipc']} />)

    await user.click(input())
    await user.keyboard('{Backspace}')

    expect(screen.queryByText('ipc')).not.toBeInTheDocument()
    expect(screen.getByText('emon')).toBeInTheDocument()
  })

  it('removes a named chip with its button', async () => {
    const user = userEvent.setup()
    render(<Harness initial={['emon']} />)

    await user.click(screen.getByRole('button', { name: REVIEW_COPY.tags.remove('emon') }))

    expect(screen.queryByText('emon')).not.toBeInTheDocument()
  })

  it('shows policy errors and associates them with the input', async () => {
    const user = userEvent.setup()
    render(<Harness initial={['emon']} />)

    await user.type(input(), 'emon{Enter}')

    expect(screen.getByRole('alert')).toHaveTextContent(REVIEW_COPY.tags.duplicate)
    expect(input()).toHaveAttribute('aria-invalid', 'true')
    expect(input()).toHaveAccessibleDescription(REVIEW_COPY.tags.duplicate)
  })

  it('focuses the input when the chip area is clicked', async () => {
    const user = userEvent.setup()
    render(<Harness initial={['emon']} />)

    await user.click(screen.getByText('Reviewer tags'))
    expect(input()).toHaveFocus()
  })
})