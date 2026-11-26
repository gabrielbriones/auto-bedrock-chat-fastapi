// The composer's slash commands. The server owns the grammar (`skills.md` "Slash commands"); this
// decides when to offer commands and turns the values filled in for a chosen one into its text.

/** The parameter that takes a command's free text: the composer textarea fills it. */
export const FREE_TEXT_PARAMETER = 'input'

export type SlashCommandParameter = {
  readonly name: string
  readonly label: string
  readonly required: boolean
  readonly inputType: 'text' | 'number' | 'select' | 'checkbox'
  readonly options: readonly string[]
  readonly placeholder: string
  readonly defaultValue: string | null
}

export type SlashCommandOption = {
  readonly name: string
  readonly title: string
  readonly description: string
  /** The command with its parameters, e.g. `/triage job_id=<Job ID> [format=md|txt]`. */
  readonly usage: string
  readonly parameters: readonly SlashCommandParameter[]
}

export type SlashCommandValues = Readonly<Record<string, string>>

const QUERY = /^\/([^\s/]*)$/
const COMMAND = /^\/([a-z0-9][a-z0-9_-]*)(?:\s|$)/

/** What the user typed after a single leading `/`, while still typing the name; `null` otherwise. */
export const slashQuery = (text: string): string | null => {
  const match = QUERY.exec(text)
  return match === null ? null : (match[1] ?? '').toLowerCase()
}

/** Name prefix matches first, then names or titles containing the query, each alphabetical. */
export const matchSlashCommands = (
  options: readonly SlashCommandOption[],
  query: string,
): readonly SlashCommandOption[] => {
  const byName = (left: SlashCommandOption, right: SlashCommandOption) => left.name.localeCompare(right.name)
  const prefixed = options.filter((option) => option.name.startsWith(query)).sort(byName)
  const containing = options
    .filter((option) => !option.name.startsWith(query))
    .filter((option) => option.name.includes(query) || option.title.toLowerCase().includes(query))
    .sort(byName)
  return [...prefixed, ...containing]
}

/** The command a draft is running (`/name` then whitespace or the end), when it is a known one. */
export const activeSlashCommand = (
  text: string,
  options: readonly SlashCommandOption[],
): SlashCommandOption | null => {
  const name = COMMAND.exec(text)?.[1]
  return options.find((option) => option.name === name) ?? null
}

/** The parameters shown as fields: all but the free-text one, which the textarea fills. */
export const parameterFields = (option: SlashCommandOption): readonly SlashCommandParameter[] =>
  option.parameters.filter((parameter) => parameter.name !== FREE_TEXT_PARAMETER)

export const freeTextParameter = (option: SlashCommandOption): SlashCommandParameter | null =>
  option.parameters.find((parameter) => parameter.name === FREE_TEXT_PARAMETER) ?? null

/** Starting values: each parameter's default, else empty (left out, so the server applies its own). */
export const initialValues = (option: SlashCommandOption): SlashCommandValues =>
  Object.fromEntries(parameterFields(option).map((parameter) => [parameter.name, parameter.defaultValue ?? '']))

/** Required parameters still blank, free text included. */
export const missingParameters = (
  option: SlashCommandOption,
  values: SlashCommandValues,
  freeText: string,
): readonly string[] =>
  option.parameters
    .filter((parameter) => parameter.required)
    .filter((parameter) => (parameter.name === FREE_TEXT_PARAMETER ? freeText : (values[parameter.name] ?? '')).trim() === '')
    .map((parameter) => parameter.name)

// Bare when it is one plain token, else double-quoted with `\` and `"` escaped.
const quoted = (value: string): string => (/^[^\s"]+$/.test(value) ? value : `"${value.replace(/[\\"]/g, '\\$&')}"`)

/** The message for `option`: every filled value as a `key=value` pair, free text as `input=`. */
export const buildSlashCommand = (option: SlashCommandOption, values: SlashCommandValues, freeText: string): string => {
  const pairs = parameterFields(option)
    .filter((parameter) => (values[parameter.name] ?? '').trim() !== '')
    .map((parameter) => `${parameter.name}=${quoted(values[parameter.name] ?? '')}`)
  if (freeTextParameter(option) !== null && freeText.trim() !== '') {
    pairs.push(`${FREE_TEXT_PARAMETER}=${quoted(freeText.trim())}`)
  }
  return [`/${option.name}`, ...pairs].join(' ')
}
