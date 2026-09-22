import { describe, expect, it } from 'vitest'

import { ScriptedConfirmationPort } from './scripted-confirmation-port'

describe('ScriptedConfirmationPort', () => {
  it('answers in the scripted order and records what was asked', async () => {
    const confirmations = new ScriptedConfirmationPort([true, 'because'])

    await expect(confirmations.confirm({ title: 'Delete?', message: '' })).resolves.toBe(true)
    await expect(confirmations.prompt({ title: 'Reason', label: 'Reason' })).resolves.toBe(
      'because',
    )
    expect(confirmations.asked.map((request) => request.title)).toEqual(['Delete?', 'Reason'])
  })

  it('treats a cancelled prompt answer as null', async () => {
    const confirmations = new ScriptedConfirmationPort([null])

    await expect(confirmations.prompt({ title: 'Reason', label: 'Reason' })).resolves.toBeNull()
  })

  it('fails loudly when the script runs out', () => {
    const confirmations = new ScriptedConfirmationPort()

    expect(() => confirmations.confirm({ title: 'Delete?', message: '' })).toThrow(
      'no scripted answer',
    )
  })
})
