import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from '@jest/globals'

import { CodeBlock } from '@/domains/messaging/presentation/CodeBlock'

describe('CodeBlock', () => {
  it('copies the raw source rather than highlighted DOM text', async () => {
    const user = userEvent.setup()
    render(<CodeBlock code={'const token = "raw"'} language="typescript" />)

    await user.click(screen.getByRole('button', { name: 'Copy code' }))

    await expect(navigator.clipboard.readText()).resolves.toBe('const token = "raw"')
  })
})