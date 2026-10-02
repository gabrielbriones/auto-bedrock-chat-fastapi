import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, jest } from '@jest/globals'

import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
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
    render(<ModelPicker catalog={catalog} selectedModelId="claude" onSelect={jest.fn()} />)
    expect(screen.getByRole('button', { name: 'Claude' })).toBeInTheDocument()
  })

  it('falls back to a generic label when nothing is selected', () => {
    render(<ModelPicker catalog={catalog} selectedModelId={null} onSelect={jest.fn()} />)
    expect(screen.getByRole('button', { name: 'Choose model' })).toBeInTheDocument()
  })

  it('lists one entry per model, collapsing regional variants into the region-less one', async () => {
    const onSelect = jest.fn<(modelId: string) => void>()
    const regional = buildModelCatalog(
      [
        { id: 'us.anthropic.opus', name: 'Opus (US)', provider: 'anthropic', supportsTemperature: true, maxOutputTokens: 4096 },
        { id: 'anthropic.opus', name: 'Opus', provider: 'anthropic', supportsTemperature: true, maxOutputTokens: 4096 },
        { id: 'global.anthropic.opus', name: 'Opus (Global)', provider: 'anthropic', supportsTemperature: true, maxOutputTokens: 4096 },
        { id: 'anthropic.haiku', name: 'Haiku', provider: 'anthropic', supportsTemperature: true, maxOutputTokens: 4096 },
      ],
      [],
    )
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<ModelPicker catalog={regional} selectedModelId="us.anthropic.opus" onSelect={onSelect} />)

    await user.click(screen.getByRole('button', { name: 'Opus (US)' }))

    const items = await screen.findAllByRole('menuitemradio')
    expect(items.map((item) => item.textContent)).toEqual(['Opus', 'Haiku'])
    expect(screen.getByRole('menuitemradio', { name: 'Opus' })).toHaveAttribute('aria-checked', 'true')

    await user.click(screen.getByRole('menuitemradio', { name: 'Haiku' }))
    expect(onSelect).toHaveBeenCalledWith('anthropic.haiku')
  })

  it('opens the model submenu without a sideways entrance animation (XMGPLAT-11804)', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<ModelPicker catalog={catalog} selectedModelId="claude" onSelect={jest.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Claude' }))
    await user.click(await screen.findByRole('menuitem', { name: 'anthropic' }))

    const submenu = (await screen.findByRole('menuitemradio', { name: 'No Temp Model' })).closest('[data-slot="dropdown-menu-sub-content"]')
    expect(submenu).not.toBeNull()
    expect(submenu?.className).not.toMatch(/slide-in-from-|zoom-in-/)
  })

  it('opens the current model family once the picker has opened (FR-CFG-003a)', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    render(<ModelPicker catalog={catalog} selectedModelId="claude" onSelect={jest.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Claude' }))

    expect(await screen.findByRole('menuitemradio', { name: 'Claude' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitemradio', { name: 'GPT' })).toBeNull()
  })

  it('supports full keyboard traversal into a second family (FR-CFG-003b)', async () => {
    const onSelect = jest.fn<(modelId: string) => void>()
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

  it('renders the footer below the families, and only the footer when not selectable', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    const footer = <DropdownMenuItem>Model settings…</DropdownMenuItem>
    const { unmount } = render(<ModelPicker catalog={catalog} selectedModelId="claude" onSelect={jest.fn()} footer={footer} />)

    await user.click(screen.getByRole('button', { name: 'Claude' }))
    expect(await screen.findByRole('menuitem', { name: 'Model settings…' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'openai' })).toBeInTheDocument()
    unmount()

    render(<ModelPicker catalog={catalog} selectedModelId="claude" onSelect={jest.fn()} footer={footer} selectable={false} />)
    await user.click(screen.getByRole('button', { name: 'Claude' }))
    expect(await screen.findByRole('menuitem', { name: 'Model settings…' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'openai' })).toBeNull()
  })
})
