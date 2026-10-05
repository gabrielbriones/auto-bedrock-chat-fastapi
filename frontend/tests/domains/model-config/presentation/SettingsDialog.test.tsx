import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, jest } from '@jest/globals'

import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { toConfigurationProfile, type ConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import type { OverrideKey, OverrideValue } from '@/domains/model-config/domain/override-key'
import { SettingsDialog } from '@/domains/model-config/presentation/SettingsDialog'

const claude = { id: 'claude', name: 'Claude', provider: 'anthropic', supportsTemperature: true, maxOutputTokens: 4096 }
const catalog = buildModelCatalog([claude], [])

const profile = (overrides: Partial<ConfigurationProfile> = {}): ConfigurationProfile => ({
  ...toConfigurationProfile({ model_id: 'claude', temperature: 0.7, enable_rag: false }, null, catalog),
  ...overrides,
})

describe('SettingsDialog', () => {
  it('renders only visibleFields, in order, leaving the model to the composer picker', () => {
    render(<SettingsDialog open profile={profile()} onOpenChange={jest.fn()} onCommit={jest.fn()} />)

    expect(screen.getByRole('dialog', { name: 'Model settings' })).toBeInTheDocument()
    expect(screen.queryByText('Model')).not.toBeInTheDocument()
    expect(screen.getByText('Temperature')).toBeInTheDocument()
    expect(screen.queryByText('Knowledge base results')).not.toBeInTheDocument()
    expect(screen.queryByText('Region')).not.toBeInTheDocument()
  })

  it('offers the current model\'s regions and commits the chosen variant as model_id', async () => {
    const onCommit = jest.fn<(key: OverrideKey, value: OverrideValue) => void>()
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    const regional = buildModelCatalog(
      [
        { ...claude, id: 'anthropic.opus', name: 'Opus' },
        { ...claude, id: 'us.anthropic.opus', name: 'Opus (US)' },
        { ...claude, id: 'global.anthropic.opus', name: 'Opus (Global)' },
      ],
      [],
    )

    render(
      <SettingsDialog
        open
        profile={toConfigurationProfile({ model_id: 'anthropic.opus' }, null, regional)}
        onOpenChange={jest.fn()}
        onCommit={onCommit}
      />,
    )

    const region = screen.getByRole('combobox', { name: 'Region' })
    expect(region).toHaveTextContent('Default')
    await user.click(region)
    await user.click(await screen.findByRole('option', { name: 'Global' }))

    expect(onCommit).toHaveBeenCalledWith('model_id', 'global.anthropic.opus')
  })

  it('clamps a max_tokens commit to the effective model cap before forwarding it (FR-CFG-005a)', async () => {
    const onCommit = jest.fn<(key: OverrideKey, value: OverrideValue) => void>()
    const user = userEvent.setup()

    render(<SettingsDialog open profile={profile()} onOpenChange={jest.fn()} onCommit={onCommit} />)

    const maxTokens = screen.getByRole('spinbutton')
    expect(maxTokens).toHaveAttribute('max', '4096')
    await user.clear(maxTokens)
    await user.type(maxTokens, '9999999')
    await user.tab()

    expect(onCommit).toHaveBeenCalledWith('max_tokens', 4096)
  })

  it('shows a pending affordance and disables only the pending field', () => {
    render(
      <SettingsDialog
        open
        profile={profile()}
        pendingKeys={new Set(['temperature'])}
        onOpenChange={jest.fn()}
        onCommit={jest.fn()}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Waiting for server confirmation')
    const sliders = screen.getAllByRole('slider', { hidden: true })
    expect(sliders.filter((slider) => slider.hasAttribute('disabled'))).toHaveLength(1)
    expect(screen.getByRole('spinbutton')).toBeEnabled()
  })

  it('surfaces the server rejection reason and dismisses it explicitly', async () => {
    const onDismissRejections = jest.fn()
    const user = userEvent.setup()

    render(
      <SettingsDialog
        open
        profile={profile()}
        rejectionReasons={["'max_tokens' exceeds the selected model limit"]}
        onOpenChange={jest.fn()}
        onCommit={jest.fn()}
        onDismissRejections={onDismissRejections}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent("'max_tokens' exceeds the selected model limit")
    await user.click(screen.getByRole('button', { name: 'Dismiss rejection' }))
    expect(onDismissRejections).toHaveBeenCalledTimes(1)
  })

  it('enables reset only for confirmed values that differ from defaults', async () => {
    const onReset = jest.fn()
    const user = userEvent.setup()
    const { rerender } = render(
      <SettingsDialog open profile={profile()} onOpenChange={jest.fn()} onCommit={jest.fn()} onReset={onReset} />,
    )

    expect(screen.getByRole('button', { name: 'Reset to defaults' })).toBeDisabled()

    rerender(
      <SettingsDialog
        open
        profile={profile({ overrides: { temperature: 0.2 } })}
        onOpenChange={jest.fn()}
        onCommit={jest.fn()}
        onReset={onReset}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Reset to defaults' }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })
})
