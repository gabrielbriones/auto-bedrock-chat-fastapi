import { describe, expect, it, jest } from '@jest/globals'

import type { Logger } from '@/shared/logging/logger'

import { PromptCatalogStore } from '@/domains/prompt-catalog/application/prompt-catalog.store'
import type { ComposedPromptSink, UrlNavigator, UrlState } from '@/domains/prompt-catalog/application/ports'
import { parsePromptCatalog } from '@/domains/prompt-catalog/domain/prompt-catalog'
import type { ComposedPrompt } from '@/domains/prompt-catalog/domain/composed-prompt'

const catalog = parsePromptCatalog(
  [
    { id: 'workload-analysis', label: 'Workload Analysis', description: '', template: 'JOB_ID = {{JOB_ID}}' },
    { id: 'health-check', label: 'Health Check', description: '', template: 'Please summarise API health.' },
  ],
  [
    {
      name: 'JOB_ID',
      label: 'Job ID',
      input_type: 'text',
      validate: '^[0-9a-f]{8}$',
      detect_pattern: '[0-9a-f]{8}',
    },
  ],
)

const silentLogger: Logger = { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() }

// A fake `UrlNavigator` whose `replace` mutates the state a later `current()` reads back, so a
// scrub is observable exactly the way the real browser adapter would behave.
const fakeNavigator = (initial: UrlState): UrlNavigator & { state: UrlState } => {
  const nav = {
    state: initial,
    current: () => nav.state,
    replace: (next: UrlState) => {
      nav.state = next
    },
  }
  return nav
}

const harness = (url: UrlState = {}) => {
  const sink: ComposedPromptSink = { submit: jest.fn() }
  const urlNavigator = fakeNavigator(url)
  const store = new PromptCatalogStore({ catalog, sink, urlNavigator, logger: silentLogger })
  return { store, sink, urlNavigator }
}

describe('PromptCatalogStore — bindings', () => {
  it('ignores a bindVariable call for a name outside the catalogue', () => {
    const { store } = harness()
    store.bindVariable('NOT_A_VARIABLE', { kind: 'text', value: 'x' })
    expect(store.getSnapshot().bindings.NOT_A_VARIABLE).toBeUndefined()
  })

  it('an explicit edit clears the detected marker for that field', () => {
    const { store } = harness()
    store.detectFromMessage('please analyze 1a2b3c4d')
    expect(store.getSnapshot().detected.has('JOB_ID')).toBe(true)

    store.bindVariable('JOB_ID', { kind: 'text', value: 'edited' })
    expect(store.getSnapshot().detected.has('JOB_ID')).toBe(false)
  })

  it('detection never overrides an already-edited field (Phase 2 accept)', () => {
    const { store } = harness()
    store.bindVariable('JOB_ID', { kind: 'text', value: 'manual' })

    store.detectFromMessage('1a2b3c4d')

    expect(store.getSnapshot().bindings.JOB_ID).toEqual({ kind: 'text', value: 'manual' })
  })

  it('notifies subscribers on every mutating call', () => {
    const { store } = harness()
    const listener = jest.fn()
    store.subscribe(listener)

    store.bindVariable('JOB_ID', { kind: 'text', value: 'x' })

    expect(listener).toHaveBeenCalledTimes(1)
  })
})

describe('PromptCatalogStore — activatePreset', () => {
  it('submits a composed prompt and reports "sent" when every variable validates', () => {
    const { store, sink } = harness()
    store.bindVariable('JOB_ID', { kind: 'text', value: '1a2b3c4d' })

    const outcome = store.activatePreset('workload-analysis')

    expect(outcome).toEqual({ kind: 'sent' })
    expect(sink.submit).toHaveBeenCalledWith<[ComposedPrompt]>({
      text: 'JOB_ID = 1a2b3c4d',
      presetId: 'workload-analysis',
      bindings: { JOB_ID: { kind: 'text', value: '1a2b3c4d' } },
    })
  })

  it('reports "blocked" with the failing variables named, and never submits', () => {
    const { store, sink } = harness()

    const outcome = store.activatePreset('workload-analysis')

    expect(outcome).toEqual({ kind: 'blocked', evaluation: { enabled: false, missing: [], invalid: ['JOB_ID'] } })
    expect(sink.submit).not.toHaveBeenCalled()
  })

  it('reports "unknown-preset" for an id the catalogue does not have', () => {
    const { store } = harness()
    expect(store.activatePreset('does-not-exist')).toEqual({ kind: 'unknown-preset' })
  })
})

describe('PromptCatalogStore — deep links', () => {
  it('prefills a binding from the URL and marks it edited (so detection will not later override it)', () => {
    const { store } = harness({ prompt: 'workload-analysis', JOB_ID: '1a2b3c4d' })

    store.tryAutoSend(false)

    expect(store.getSnapshot().bindings.JOB_ID).toEqual({ kind: 'text', value: '1a2b3c4d' })
  })

  it('does not send while not ready, even with a fully valid prefill', () => {
    const { store, sink } = harness({ prompt: 'workload-analysis', JOB_ID: '1a2b3c4d' })

    store.tryAutoSend(false)

    expect(sink.submit).not.toHaveBeenCalled()
  })

  it('sends exactly once as soon as it becomes ready, whatever readiness does afterwards', () => {
    const { store, sink } = harness({ prompt: 'workload-analysis', JOB_ID: '1a2b3c4d' })

    store.tryAutoSend(false)
    store.tryAutoSend(true)
    expect(sink.submit).toHaveBeenCalledTimes(1)

    // Re-renders and reconnect flapping must not fire it again.
    for (let i = 0; i < 5; i += 1) {
      store.tryAutoSend(true)
    }
    store.tryAutoSend(false)
    store.tryAutoSend(true)

    expect(sink.submit).toHaveBeenCalledTimes(1)
  })

  it('autosend=0 pre-fills but never sends, however many times readiness changes', () => {
    const { store, sink } = harness({ prompt: 'workload-analysis', JOB_ID: '1a2b3c4d', autosend: '0' })

    store.tryAutoSend(false)
    store.tryAutoSend(true)
    store.tryAutoSend(true)

    expect(store.getSnapshot().bindings.JOB_ID).toEqual({ kind: 'text', value: '1a2b3c4d' })
    expect(sink.submit).not.toHaveBeenCalled()
  })

  it('an invalid deep-link value never sends and logs a diagnostic naming it', () => {
    const { store, sink } = harness({ prompt: 'workload-analysis', JOB_ID: '' })

    store.tryAutoSend(true)

    expect(sink.submit).not.toHaveBeenCalled()
    expect(silentLogger.warn).toHaveBeenCalledWith('prompt_deep_link_not_ready', { missing: [], invalid: ['JOB_ID'] })
  })

  it('an unknown preset id applies nothing and logs a diagnostic (FR-PROMPT-008b)', () => {
    const { store, sink } = harness({ prompt: 'does-not-exist' })

    store.tryAutoSend(true)

    expect(sink.submit).not.toHaveBeenCalled()
    expect(silentLogger.warn).toHaveBeenCalledWith('prompt_deep_link_unknown_preset', { presetId: 'does-not-exist' })
  })

  it('scrubs prompt/autosend/consumed variable keys from the URL after sending, keeping unrelated params', () => {
    const { store, urlNavigator } = harness({ prompt: 'workload-analysis', JOB_ID: '1a2b3c4d', keep: 'me' })

    store.tryAutoSend(true)

    expect(urlNavigator.state).toEqual({ keep: 'me' })
  })

  it('a page with no prompt param is a complete no-op', () => {
    const { store, sink, urlNavigator } = harness({ keep: 'me' })

    store.tryAutoSend(true)

    expect(sink.submit).not.toHaveBeenCalled()
    expect(urlNavigator.state).toEqual({ keep: 'me' })
  })

  // FR-PROMPT-016 / Phase 2 accept: the single-fire regression matrix.
  describe('single-fire guarantee', () => {
    it('back-navigation: the browser restoring the pre-scrub URL still never resends', () => {
      const { store, sink, urlNavigator } = harness({ prompt: 'workload-analysis', JOB_ID: '1a2b3c4d' })

      store.tryAutoSend(true)
      expect(sink.submit).toHaveBeenCalledTimes(1)

      // Simulate a back-navigation restoring the original, un-scrubbed query string.
      urlNavigator.state = { prompt: 'workload-analysis', JOB_ID: '1a2b3c4d' }
      store.tryAutoSend(true)

      expect(sink.submit).toHaveBeenCalledTimes(1)
    })

    it('a fresh reload (new store instance) with the already-scrubbed URL sends nothing', () => {
      // What actually prevents a real reload from resending: the URL itself has no `prompt` left.
      const { store, sink } = harness({ keep: 'me' })

      store.tryAutoSend(true)

      expect(sink.submit).not.toHaveBeenCalled()
    })
  })
})
