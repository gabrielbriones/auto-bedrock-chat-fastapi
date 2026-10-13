import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, jest } from '@jest/globals'

import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { overrideFieldFor } from '@/domains/model-config/domain/override-field'
import type { OverrideKey, OverrideValue } from '@/domains/model-config/domain/override-key'
import { OverrideField } from '@/domains/model-config/presentation/OverrideField'

const catalog = buildModelCatalog(
  [{ id: 'claude', name: 'Claude', provider: 'anthropic', supportsTemperature: true, maxOutputTokens: 4096 }],
  [],
)

describe('OverrideField', () => {
  it('commits a switch field immediately on toggle (no intermediate state to buffer)', async () => {
    const onCommit = jest.fn<(key: OverrideKey, value: OverrideValue) => void>()
    const user = userEvent.setup()

    render(
      <OverrideField field={overrideFieldFor('enable_rag')} value={false} overridden={false} catalog={catalog} onCommit={onCommit} />,
    )

    await user.click(screen.getByRole('switch'))

    expect(onCommit).toHaveBeenCalledWith('enable_rag', true)
  })

  it('commits a number field on blur, not on every keystroke (FR-CFG-012)', async () => {
    const onCommit = jest.fn<(key: OverrideKey, value: OverrideValue) => void>()
    const user = userEvent.setup()

    render(
      <OverrideField field={overrideFieldFor('max_tokens')} value={100} overridden={false} catalog={catalog} onCommit={onCommit} />,
    )

    const field = screen.getByRole('spinbutton')
    await user.clear(field)
    await user.type(field, '512')
    expect(onCommit).not.toHaveBeenCalled()

    await user.tab()
    expect(onCommit).toHaveBeenCalledWith('max_tokens', 512)
  })

  it('ignores a blur with an unparseable number', async () => {
    const onCommit = jest.fn<(key: OverrideKey, value: OverrideValue) => void>()
    const user = userEvent.setup()

    render(
      <OverrideField field={overrideFieldFor('max_tokens')} value={100} overridden={false} catalog={catalog} onCommit={onCommit} />,
    )

    const field = screen.getByRole('spinbutton')
    await user.clear(field)
    await user.tab()

    expect(onCommit).not.toHaveBeenCalled()
  })

  it('renders a model picker for the model_id field and commits on selection', async () => {
    const onCommit = jest.fn<(key: OverrideKey, value: OverrideValue) => void>()
    const user = userEvent.setup()

    render(
      <OverrideField field={overrideFieldFor('model_id')} value={null as unknown as OverrideValue} overridden={false} catalog={catalog} onCommit={onCommit} />,
    )

    screen.getByRole('button', { name: 'Choose model' }).focus()
    await user.keyboard('[Enter]')
    await user.keyboard('[ArrowRight]')
    await user.keyboard('[Enter]')

    expect(onCommit).toHaveBeenCalledWith('model_id', 'claude')
  })

  it('commits a slider field on keyboard interaction (FR-CFG-012)', async () => {
    const onCommit = jest.fn<(key: OverrideKey, value: OverrideValue) => void>()
    const user = userEvent.setup()

    render(
      <OverrideField field={overrideFieldFor('temperature')} value={0.5} overridden={false} catalog={catalog} onCommit={onCommit} />,
    )

    const slider = screen.getByRole('slider', { hidden: true })
    slider.focus()
    await user.keyboard('[ArrowRight]')

    expect(onCommit).toHaveBeenCalledWith('temperature', 0.6)
  })

  it('marks an overridden field distinctly from the label text', () => {
    const { container } = render(
      <OverrideField field={overrideFieldFor('enable_rag')} value={true} overridden catalog={catalog} onCommit={jest.fn()} />,
    )

    expect(container.querySelector('[data-slot="override-marker"]')).not.toBeNull()
  })
})
