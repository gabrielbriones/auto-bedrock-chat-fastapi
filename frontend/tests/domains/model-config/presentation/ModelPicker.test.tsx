import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { ModelPicker } from '@/domains/model-config/presentation/ModelPicker'

const catalog = buildModelCatalog(
  [
    { id: 'claude', name: 'Claude', provider: 'anthropic', supportsTemperature: true, maxOutputTokens: 4096 },
    { id: 'no-temp', name: 'No Temp Model', provider: 'anthropic', supportsTemperature: false, maxOutputTokens: 1000 },
    { id: 'gpt', name: 'GPT', provider: 'openai', supportsTemperature: true, maxOutputTokens: 8192 },
  ],
  [],
)

describe('ModelPicker', () => {
  it('shows the current model name on the trigger', () => {
    render(<ModelPicker catalog={catalog} selectedModelId="claude" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Claude' })).toBeInTheDocument()
  })

  it('falls back to a generic label when nothing is selected', () => {
    render(<ModelPicker catalog={catalog} selectedModelId={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Choose model' })).toBeInTheDocument()
  })

  it('marks a model with no temperature support (FR-CFG-017)', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<ModelPicker catalog={catalog} selectedModelId="claude" onSelect={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Claude' }))
    await user.click(await screen.findByRole('menuitem', { name: 'anthropic' }))

    expect(await screen.findByText('No temperature support')).toBeInTheDocument()
  })

  it('supports full keyboard traversal into a second family (FR-CFG-003b)', async () => {
    const onSelect = vi.fn<(modelId: string) => void>()
    const user = userEvent.setup()

    render(<ModelPicker catalog={catalog} selectedModelId="claude" onSelect={onSelect} />)

    screen.getByRole('button', { name: 'Claude' }).focus()
    await user.keyboard('[Enter]')
    await user.keyboard('[ArrowDown]')
    await user.keyboard('[ArrowRight]')
    await user.keyboard('[ArrowDown]')
    await user.keyboard('[Enter]')

    expect(onSelect).toHaveBeenCalledWith('gpt')
  })
})
