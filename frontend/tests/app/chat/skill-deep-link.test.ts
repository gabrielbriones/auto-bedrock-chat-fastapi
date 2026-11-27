import { describe, expect, it, jest } from '@jest/globals'

import { SkillDeepLink } from '@/app/chat/skill-deep-link'
import type { SlashCommandOption } from '@/domains/messaging/domain/public'
import type { UrlNavigator, UrlState } from '@/domains/prompt-catalog/application/ports'

const TRIAGE: SlashCommandOption = {
  name: 'triage',
  title: 'Triage',
  description: '',
  usage: '/triage job_id=<Job ID>',
  parameters: ['job_id', 'input'].map((name) => ({
    name,
    label: name,
    required: false,
    inputType: 'text' as const,
    options: [],
    placeholder: '',
    defaultValue: null,
  })),
}

const setup = (initial: UrlState) => {
  const urlNavigator: UrlNavigator & { state: UrlState } = {
    state: initial,
    current: () => urlNavigator.state,
    replace: (next) => {
      urlNavigator.state = next
    },
  }
  const send = jest.fn<(text: string) => void>()
  const legacy = { tryAutoSend: jest.fn<(ready: boolean) => void>() }
  const logger = { debug: jest.fn(), info: jest.fn(), warn: jest.fn(), error: jest.fn() }
  const link = new SkillDeepLink({ urlNavigator, send, legacy, logger })
  return { link, urlNavigator, send, legacy, logger }
}

describe('SkillDeepLink', () => {
  it('waits for the catalog and a ready composer, then sends once and scrubs the link', () => {
    const { link, urlNavigator, send } = setup({ skill: 'triage', job_id: 'j 1', input: 'why?', keep: 'me' })

    link.resolve([], true)
    link.resolve([TRIAGE], false)
    expect(send).not.toHaveBeenCalled()

    link.resolve([TRIAGE], true)
    link.resolve([TRIAGE], true)

    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith('/triage job_id="j 1" input=why?')
    expect(urlNavigator.state).toEqual({ keep: 'me' })
  })

  it('puts the command in the composer with autosend=0, without waiting for readiness', () => {
    const { link, send } = setup({ prompt: 'triage', JOB_ID: '7', autosend: '0' })
    const listener = jest.fn()
    link.subscribe(listener)

    link.resolve([TRIAGE], false)

    expect(send).not.toHaveBeenCalled()
    expect(listener).toHaveBeenCalledTimes(1)
    expect(link.getSnapshot()).toEqual({ text: '/triage job_id=7' })
  })

  it('hands a ?prompt= no skill answers to the preset deep link', () => {
    const { link, legacy, send } = setup({ prompt: 'old-preset' })

    link.resolve([TRIAGE], true)

    expect(legacy.tryAutoSend).toHaveBeenCalledWith(true)
    expect(send).not.toHaveBeenCalled()
  })

  it('logs an unknown ?skill= once and leaves it', () => {
    const { link, logger, send } = setup({ skill: 'unknown' })

    link.resolve([TRIAGE], true)
    link.resolve([TRIAGE], true)

    expect(logger.warn).toHaveBeenCalledTimes(1)
    expect(send).not.toHaveBeenCalled()
  })
})
