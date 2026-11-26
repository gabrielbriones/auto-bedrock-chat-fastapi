// The composer's slash-command menu. The server owns the grammar (`skills.md` "Slash commands");
// this only decides when to offer commands and what selecting one inserts.

export type SlashCommandOption = {
  readonly name: string
  readonly title: string
  readonly description: string
  /** The command with its parameters, e.g. `/triage job_id=<Job ID> [format=md|txt]`. */
  readonly usage: string
}

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

/** The draft after choosing `option`: the command name and a space, ready for its parameters. */
export const completeSlashCommand = (option: SlashCommandOption): string => `/${option.name} `
