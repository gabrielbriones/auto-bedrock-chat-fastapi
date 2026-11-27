import { describe, expect, it } from '@jest/globals'

import {
  activeSlashCommand,
  buildSlashCommand,
  initialValues,
  matchSlashCommands,
  missingParameters,
  slashQuery,
  type SlashCommandOption,
  type SlashCommandParameter,
} from '@/domains/messaging/domain/slash-command'

const parameter = (name: string, extra: Partial<SlashCommandParameter> = {}): SlashCommandParameter => ({
  name,
  label: name,
  required: false,
  inputType: 'text',
  options: [],
  placeholder: '',
  defaultValue: null,
  ...extra,
})

const option = (name: string, title = name, parameters: readonly SlashCommandParameter[] = []): SlashCommandOption => ({
  name,
  title,
  description: '',
  usage: `/${name}`,
  parameters,
})

const OPTIONS = [option('triage', 'Job triage'), option('summarize', 'Summary'), option('job-report', 'Report')]

const TRIAGE = option('triage', 'Job triage', [
  parameter('job_id', { required: true }),
  parameter('format', { inputType: 'select', options: ['md', 'txt'], defaultValue: 'md' }),
  parameter('note'),
])

describe('slashQuery', () => {
  it.each([
    ['/', ''],
    ['/tri', 'tri'],
    ['/TRI', 'tri'],
    ['/job-r', 'job-r'],
  ])('reads %j as the query %j', (text, query) => {
    expect(slashQuery(text)).toBe(query)
  })

  it.each(['', 'hello', '//', '//tri', '/tri ', '/triage job_id=1', ' /tri', '/usr/bin'])('offers nothing for %j', (text) => {
    expect(slashQuery(text)).toBeNull()
  })
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
    expect(activeSlashCommand('//triage', OPTIONS)).toBeNull()
  })
})

describe('parameter values', () => {
  it('starts from each default, else blank', () => {
    expect(initialValues(TRIAGE)).toEqual({ job_id: '', format: 'md', note: '' })
  })

  it('starts a required checkbox without a default as false, an optional one as left out', () => {
    const flags = option('flags', 'Flags', [
      parameter('strict', { inputType: 'checkbox', required: true }),
      parameter('verbose', { inputType: 'checkbox' }),
    ])

    expect(initialValues(flags)).toEqual({ strict: 'false', verbose: '' })
    expect(missingParameters(flags, initialValues(flags), '')).toEqual([])
  })

  it('reports blank required parameters, free text included', () => {
    const withInput = option('ask', 'Ask', [parameter('input', { required: true })])

    expect(missingParameters(TRIAGE, { job_id: ' ' }, '')).toEqual(['job_id'])
    expect(missingParameters(TRIAGE, { job_id: 'j-1' }, '')).toEqual([])
    expect(missingParameters(withInput, {}, '')).toEqual(['input'])
    expect(missingParameters(withInput, {}, 'why?')).toEqual([])
  })
})

describe('buildSlashCommand', () => {
  it('sends filled values as pairs, quoting anything that is not one plain token', () => {
    expect(buildSlashCommand(TRIAGE, { job_id: 'j-1', format: 'md', note: 'say "hi" \\ bye' }, '')).toBe(
      '/triage job_id=j-1 format=md note="say \\"hi\\" \\\\ bye"',
    )
  })

  it('leaves blank values out so the server applies its defaults', () => {
    expect(buildSlashCommand(TRIAGE, { job_id: 'j-1', format: '', note: '  ' }, '')).toBe('/triage job_id=j-1')
  })

  it('sends free text as the input parameter only when the skill takes it', () => {
    const ask = option('ask', 'Ask', [parameter('input')])

    expect(buildSlashCommand(ask, {}, ' why did it fail? ')).toBe('/ask input="why did it fail?"')
    expect(buildSlashCommand(TRIAGE, { job_id: 'j-1' }, 'ignored')).toBe('/triage job_id=j-1')
  })
})
