import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { buildModelCatalog } from '@/domains/model-config/domain/model-catalog'
import { toConfigurationProfile, type ConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import type { OverrideKey, OverrideValue } from '@/domains/model-config/domain/override-key'
import { SettingsSheet } from '@/domains/model-config/presentation/SettingsSheet'

const claude = { id: 'claude', name: 'Claude', provider: 'anthropic', supportsTemperature: true, maxOutputTokens: 4096 }
const catalog = buildModelCatalog([claude], [])

const profile = (overrides: Partial<ConfigurationProfile> = {}): ConfigurationProfile => ({
  ...toConfigurationProfile({ model_id: 'claude', temperature: 0.7, enable_rag: false }, null, catalog),
  ...overrides,
})

describe('SettingsSheet', () => {
  it('renders only visibleFields, in order', () => {
    render(<SettingsSheet open profile={profile()} onOpenChange={vi.fn()} onCommit={vi.fn()} />)

    expect(screen.getByText('Model')).toBeInTheDocument()
    expect(screen.getByText('Temperature')).toBeInTheDocument()
    expect(screen.queryByText('Knowledge base results')).not.toBeInTheDocument()
  })

  it('renders nothing when closed (Base UI Dialog unmounts the popup)', () => {
    render(<SettingsSheet open={false} profile={profile()} onOpenChange={vi.fn()} onCommit={vi.fn()} />)
    expect(screen.queryByText('Model settings')).not.toBeInTheDocument()
  })

  it('clamps a max_tokens commit to the effective model cap before forwarding it (FR-CFG-005a)', async () => {
    const onCommit = vi.fn<(key: OverrideKey, value: OverrideValue) => void>()
    const user = userEvent.setup()

    render(<SettingsSheet open profile={profile()} onOpenChange={vi.fn()} onCommit={onCommit} />)

    const maxTokens = screen.getByRole('spinbutton')
    expect(maxTokens).toHaveAttribute('max', '4096')
    await user.clear(maxTokens)
    await user.type(maxTokens, '9999999')
    await user.tab()

    expect(onCommit).toHaveBeenCalledWith('max_tokens', 4096)
  })

  it('shows a pending affordance and disables only the pending field', () => {
    render(
      <SettingsSheet
        open
        profile={profile()}
        pendingKeys={new Set(['temperature'])}
        onOpenChange={vi.fn()}
        onCommit={vi.fn()}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Waiting for server confirmation')
    const sliders = screen.getAllByRole('slider', { hidden: true })
    expect(sliders.filter((slider) => slider.hasAttribute('disabled'))).toHaveLength(1)
    expect(screen.getByRole('spinbutton')).toBeEnabled()
  })

  it('surfaces the server rejection reason and dismisses it explicitly', async () => {
    const onDismissRejections = vi.fn()
    const user = userEvent.setup()

    render(
      <SettingsSheet
        open
        profile={profile()}
        rejectionReasons={["'max_tokens' exceeds the selected model limit"]}
        onOpenChange={vi.fn()}
        onCommit={vi.fn()}
        onDismissRejections={onDismissRejections}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent("'max_tokens' exceeds the selected model limit")
    await user.click(screen.getByRole('button', { name: 'Dismiss rejection' }))
    expect(onDismissRejections).toHaveBeenCalledOnce()
  })

  it('enables reset only for confirmed values that differ from defaults', async () => {
    const onReset = vi.fn()
    const user = userEvent.setup()
    const { rerender } = render(
      <SettingsSheet open profile={profile()} onOpenChange={vi.fn()} onCommit={vi.fn()} onReset={onReset} />,
    )

    expect(screen.getByRole('button', { name: 'Reset to defaults' })).toBeDisabled()

    rerender(
      <SettingsSheet
        open
        profile={profile({ overrides: { temperature: 0.2 } })}
        onOpenChange={vi.fn()}
        onCommit={vi.fn()}
        onReset={onReset}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Reset to defaults' }))
    expect(onReset).toHaveBeenCalledOnce()
  })
})
