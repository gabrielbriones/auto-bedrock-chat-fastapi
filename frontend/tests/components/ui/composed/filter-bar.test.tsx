import { describe, expect, it, jest } from '@jest/globals'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import {
  DebouncedFilterInput,
  FILTER_DEBOUNCE_MS,
  FilterBar,
  FilterField,
} from '@/components/ui/composed/filter-bar'
import { ADMIN_COPY } from '@/shared/copy/admin'

// FR-REV-002 fixes the settle time the legacy dashboard used; it is asserted so a future tweak is
// a deliberate change rather than a drift.
describe('filter debounce', () => {
  it('settles at 200 ms', () => {
    expect(FILTER_DEBOUNCE_MS).toBe(200)
  })
})

describe('FilterBar', () => {
  it('is a named search landmark with no Apply action', () => {
    render(
      <FilterBar>
        <p>fields</p>
      </FilterBar>,
    )

    expect(screen.getByRole('search', { name: ADMIN_COPY.filters.label })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /apply/i })).not.toBeInTheDocument()
  })

  it('offers a reset only when one is wired', async () => {
    const onReset = jest.fn()
    const user = userEvent.setup()
    render(
      <FilterBar onReset={onReset}>
        <p>fields</p>
      </FilterBar>,
    )

    await user.click(screen.getByRole('button', { name: ADMIN_COPY.filters.reset }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('associates each field with its label', () => {
    render(
      <FilterBar>
        <FilterField label="Tags">{(id) => <input id={id} />}</FilterField>
      </FilterBar>,
    )

    expect(screen.getByLabelText('Tags')).toBeInTheDocument()
  })
})

describe('DebouncedFilterInput', () => {
  it('commits once after typing settles, not per keystroke', async () => {
    const onCommit = jest.fn()
    const user = userEvent.setup()

    render(<DebouncedFilterInput id="tags" value="" onCommit={onCommit} delayMs={20} />)

    await user.type(screen.getByRole('textbox'), 'emon')

    await waitFor(() => {
      expect(onCommit).toHaveBeenCalledWith('emon')
    })
    expect(onCommit).toHaveBeenCalledTimes(1)
  })

  it('does not re-commit a value that already matches the applied one', async () => {
    const onCommit = jest.fn()

    render(<DebouncedFilterInput id="tags" value="emon" onCommit={onCommit} delayMs={5} />)

    await new Promise((resolve) => {
      setTimeout(resolve, 50)
    })

    expect(onCommit).not.toHaveBeenCalled()
  })
})
