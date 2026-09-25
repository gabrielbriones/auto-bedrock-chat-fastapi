import { describe, expect, it } from '@jest/globals'

import { parseDeepLink, scrubDeepLink } from '@/domains/prompt-catalog/domain/deep-link'
import { parsePromptCatalog } from '@/domains/prompt-catalog/domain/prompt-catalog'

const catalog = parsePromptCatalog(
  [
    { id: 'workload-analysis', label: 'Workload Analysis', description: '', template: '{{JOB_ID}}' },
    { id: 'regression-check', label: 'Regression Check', description: '', template: '{{JOB_ID}} {{NEW_JOB_ID}}' },
  ],
  [{ name: 'JOB_ID' }, { name: 'NEW_JOB_ID' }],
)

describe('parseDeepLink', () => {
  it('selects the preset and pre-fills its required variables from matching query keys', () => {
    expect(parseDeepLink({ prompt: 'workload-analysis', JOB_ID: '123' }, catalog)).toEqual({
      presetId: 'workload-analysis',
      bindings: { JOB_ID: '123' },
      autoSend: true,
    })
  })

  it('defaults autoSend to true when absent', () => {
    expect(parseDeepLink({ prompt: 'workload-analysis' }, catalog)?.autoSend).toBe(true)
  })

  it.each(['0', 'false'])('autosend=%s suppresses auto-send', (value) => {
    expect(parseDeepLink({ prompt: 'workload-analysis', autosend: value }, catalog)?.autoSend).toBe(false)
  })

  it('ignores a query key that is not one of the preset\'s required variables', () => {
    expect(parseDeepLink({ prompt: 'workload-analysis', UNRELATED: 'x' }, catalog)?.bindings).toEqual({})
  })

  it('an absent prompt param parses to null', () => {
    expect(parseDeepLink({}, catalog)).toBeNull()
  })

  it('an unknown preset id parses to null (FR-PROMPT-008b)', () => {
    expect(parseDeepLink({ prompt: 'does-not-exist' }, catalog)).toBeNull()
  })

  it('only pre-fills the variables the selected preset actually requires', () => {
    expect(parseDeepLink({ prompt: 'regression-check', JOB_ID: 'a', NEW_JOB_ID: 'b' }, catalog)?.bindings).toEqual({
      JOB_ID: 'a',
      NEW_JOB_ID: 'b',
    })
  })
})

describe('scrubDeepLink', () => {
  it('removes prompt, autosend, and every consumed variable key', () => {
    const url = { prompt: 'workload-analysis', autosend: '0', JOB_ID: '123', keep: 'me' }
    const intent = parseDeepLink(url, catalog)
    if (intent === null) throw new Error('fixture must parse')

    expect(scrubDeepLink(url, intent)).toEqual({ keep: 'me' })
  })

  it('leaves an unrelated variable key alone even if it shares a name with another preset\'s variable', () => {
    const url = { prompt: 'workload-analysis', JOB_ID: '1', NEW_JOB_ID: 'unrelated-to-this-preset' }
    const intent = parseDeepLink(url, catalog)
    if (intent === null) throw new Error('fixture must parse')

    // workload-analysis never consumed NEW_JOB_ID, so scrubbing it must survive.
    expect(scrubDeepLink(url, intent)).toEqual({ NEW_JOB_ID: 'unrelated-to-this-preset' })
  })
})
