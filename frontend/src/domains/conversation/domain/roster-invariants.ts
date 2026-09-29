import { byRecencyThenId } from '@/domains/conversation/domain/conversation'
import type { ConversationRoster } from '@/domains/conversation/domain/roster'

// DESIGN-001 §4.2 states I1…I4 in prose. This states them in code, so a property test can assert
// them over arbitrary operation sequences instead of over the handful of cases someone thought of.
export type RosterInvariant =
  | 'I1-ordered-newest-first'
  | 'I2-selection-subset-of-items'
  | 'I4-active-id-is-listed'
  | 'I5-ids-are-unique'

const orderedNewestFirst = (roster: ConversationRoster): boolean =>
  roster.items.every(
    (item, index) => index === 0 || byRecencyThenId(roster.items[index - 1] ?? item, item) <= 0,
  )

// I3 is not checkable from a single state — "at most one in flight" is a property of the sequence,
// and `beginBulkDelete` returning an error on the second call is what the property test asserts.
export const rosterViolations = (roster: ConversationRoster): readonly RosterInvariant[] => {
  const ids = roster.items.map((item) => item.id)
  const present = new Set(ids)
  const violations: RosterInvariant[] = []

  if (!orderedNewestFirst(roster)) {
    violations.push('I1-ordered-newest-first')
  }

  if ([...roster.selection].some((id) => !present.has(id))) {
    violations.push('I2-selection-subset-of-items')
  }

  if (roster.activeId !== null && !present.has(roster.activeId)) {
    violations.push('I4-active-id-is-listed')
  }

  if (present.size !== ids.length) {
    violations.push('I5-ids-are-unique')
  }

  return violations
}
