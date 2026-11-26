import { describe, expect, it } from '@jest/globals'

import {
  activeSlashCommand,
  completeSlashCommand,
  matchSlashCommands,
  slashQuery,
  type SlashCommandOption,
} from '@/domains/messaging/domain/slash-command'

const option = (name: string, title = name): SlashCommandOption => ({ name, title, description: '', usage: `/${name}` })

const OPTIONS = [option('triage', 'Job triage'), option('summarize', 'Summary'), option('job-report', 'Report')]

describe('slashQuery', () => {
  it.each([
    ['/', ''],
    ['/tri', 'tri'],
    ['/TRI', 'tri'],
    ['/job-r', 'job-r'],
  ])('reads %j as the query %j', (text, query) => {
    expect(slashQuery(text)).toBe(query)
  })

  it.each(['', 'hello', '//', '//tri', '/tri ', '/triage job_id=1', ' /tri', '/usr/bin'])(
    'offers nothing for %j',
    (text) => {
      expect(slashQuery(text)).toBeNull()
    },
  )
})

describe('matchSlashCommands', () => {
  it('lists every option for an empty query, alphabetically', () => {
    expect(matchSlashCommands(OPTIONS, '').map((o) => o.name)).toEqual(['job-report', 'summarize', 'triage'])
  })

  it('puts name prefixes before names or titles containing the query', () => {
    expect(matchSlashCommands(OPTIONS, 'job').map((o) => o.name)).toEqual(['job-report', 'triage'])
  })

  it('returns nothing when no option matches', () => {
    expect(matchSlashCommands(OPTIONS, 'zzz')).toEqual([])
  })
})

describe('activeSlashCommand', () => {
  it('finds a known command followed by whitespace or the end', () => {
    expect(activeSlashCommand('/triage job_id=1', OPTIONS)?.name).toBe('triage')
    expect(activeSlashCommand('/triage', OPTIONS)?.name).toBe('triage')
  })

  it('ignores unknown commands and partial names', () => {
    expect(activeSlashCommand('/tri', OPTIONS)).toBeNull()
    expect(activeSlashCommand('/nope x', OPTIONS)).toBeNull()
    expect(activeSlashCommand('//triage', OPTIONS)).toBeNull()
  })
})

describe('completeSlashCommand', () => {
  it('inserts the name and a space for the parameters', () => {
    expect(completeSlashCommand(option('triage'))).toBe('/triage ')
  })
})
