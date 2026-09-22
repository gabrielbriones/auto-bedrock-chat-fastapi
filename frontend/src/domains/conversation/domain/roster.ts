import type { ConversationId } from '@/shared/kernel/branded'
import type { Instant } from '@/shared/kernel/instant'
import { err, ok, type Result } from '@/shared/kernel/result'

import {
  byRecencyThenId,
  createConversation,
  mergeConversation,
  type Conversation,
} from '@/domains/conversation/domain/conversation'

// DESIGN-001 §4.2. The sidebar list is an aggregate because it enforces rules across items that the
// legacy client scattered over five methods. Every operation returns a normalised roster, so I1,
// I2 and I4 hold by construction rather than by each caller remembering to re-sort and re-prune.
export type ConversationRoster = {
  readonly items: readonly Conversation[]
  readonly activeId: ConversationId | null
  readonly selection: ReadonlySet<ConversationId>
  /** Non-null while a bulk delete is in flight; holds the *requested* ids (I3, FR-CONV-006a). */
  readonly pendingBulkDelete: ReadonlySet<ConversationId> | null
  /** The first turn of a new thread was sent before the server had named it (FR-CONV-010). */
  readonly awaitingIdForFirstTurn: boolean
}

export type BulkDeleteInFlight = { readonly kind: 'bulk-delete-in-flight' }

export type SelectionState = 'none' | 'partial' | 'all'

export const emptyRoster: ConversationRoster = {
  items: [],
  activeId: null,
  selection: new Set(),
  pendingBulkDelete: null,
  awaitingIdForFirstTurn: false,
}

const idsOf = (items: readonly Conversation[]): ReadonlySet<ConversationId> =>
  new Set(items.map((item) => item.id))

// The single gate every operation leaves through: sorts (I1), drops selection entries whose
// conversation is gone (I2, FR-CONV-007), and clears an active id that no longer exists (I4).
const normalise = (roster: ConversationRoster): ConversationRoster => {
  const items = [...roster.items].sort(byRecencyThenId)
  const present = idsOf(items)

  return {
    items,
    activeId: roster.activeId !== null && present.has(roster.activeId) ? roster.activeId : null,
    selection: new Set([...roster.selection].filter((id) => present.has(id))),
    pendingBulkDelete: roster.pendingBulkDelete,
    awaitingIdForFirstTurn: roster.awaitingIdForFirstTurn,
  }
}

export const hasConversation = (roster: ConversationRoster, id: ConversationId): boolean =>
  roster.items.some((item) => item.id === id)

export const findConversation = (
  roster: ConversationRoster,
  id: ConversationId,
): Conversation | undefined => roster.items.find((item) => item.id === id)

/** FR-CONV-010 / FR-CONV-016: the head of the list once I1 has ordered it. */
export const mostRecent = (roster: ConversationRoster): Conversation | undefined => roster.items[0]

// FR-CONV-016: the roster is a single page, so a list frame replaces it wholesale rather than
// merging. Anything the server dropped disappears here, and normalisation prunes what referenced it.
export const replaceItems = (
  roster: ConversationRoster,
  items: readonly Conversation[],
): ConversationRoster => normalise({ ...roster, items })

// FR-CONV-008: an unknown id is inserted, a known id merged. `at` dates an *insertion* only —
// merging never bumps `updatedAt` unless the patch says so, so merely opening a conversation does
// not reorder the list under the pointer that opened it. Idempotent: replaying a frame merges
// identical values onto themselves, which is why out-of-order frames converge.
export const upsert = (
  roster: ConversationRoster,
  id: ConversationId,
  patch: Partial<Omit<Conversation, 'id'>>,
  at: Instant,
): ConversationRoster => {
  const existing = findConversation(roster, id)
  const next =
    existing === undefined
      ? createConversation(id, patch.updatedAt ?? at, patch.title ?? null, patch.messageCount ?? 0)
      : mergeConversation(existing, patch)

  return replaceItems(roster, [...roster.items.filter((item) => item.id !== id), next])
}

export const removeMany = (
  roster: ConversationRoster,
  ids: Iterable<ConversationId>,
): ConversationRoster => {
  const removed = new Set(ids)

  return replaceItems(
    roster,
    roster.items.filter((item) => !removed.has(item.id)),
  )
}

export const clearItems = (roster: ConversationRoster): ConversationRoster =>
  normalise({ ...roster, items: [], awaitingIdForFirstTurn: false })

// I4 is a hard invariant, so an id the roster has never heard of is not activated. The deep-link
// path (FR-CONV-011) loads first and activates on `conversation_loaded`, by which point it exists.
export const activate = (roster: ConversationRoster, id: ConversationId): ConversationRoster =>
  hasConversation(roster, id)
    ? { ...roster, activeId: id, awaitingIdForFirstTurn: false }
    : roster

export const clearActive = (roster: ConversationRoster): ConversationRoster => ({
  ...roster,
  activeId: null,
})

/** FR-CONV-002: a new thread has no id until the server answers the first turn. */
export const awaitFirstTurnId = (roster: ConversationRoster): ConversationRoster => ({
  ...roster,
  activeId: null,
  awaitingIdForFirstTurn: true,
})

// FR-CONV-010: a refresh that arrives with nothing active, after a turn was sent into an unnamed
// thread, adopts the most recently updated conversation — that is the thread the turn created.
export const recoverActiveAfterRefresh = (roster: ConversationRoster): ConversationRoster => {
  const candidate = mostRecent(roster)

  return roster.awaitingIdForFirstTurn && roster.activeId === null && candidate !== undefined
    ? activate(roster, candidate.id)
    : roster
}

const withSelection = (
  roster: ConversationRoster,
  selection: ReadonlySet<ConversationId>,
): ConversationRoster => normalise({ ...roster, selection })

export const toggleSelection = (
  roster: ConversationRoster,
  id: ConversationId,
): ConversationRoster => {
  const selection = new Set(roster.selection)

  if (!selection.delete(id)) {
    selection.add(id)
  }

  return withSelection(roster, selection)
}

export const selectAll = (roster: ConversationRoster): ConversationRoster =>
  withSelection(roster, idsOf(roster.items))

export const clearSelection = (roster: ConversationRoster): ConversationRoster =>
  withSelection(roster, new Set())

// FR-CONV-006: drives the select-all control's indeterminate state. An empty roster is `none`, so
// the control is never shown as "all selected" with nothing to delete.
export const selectionState = (roster: ConversationRoster): SelectionState => {
  if (roster.selection.size === 0) {
    return 'none'
  }

  return roster.selection.size === roster.items.length ? 'all' : 'partial'
}

// I3. The guard holds the requested ids rather than a bare boolean, because FR-CONV-006a clears the
// *requested* set from the selection — ids the server skipped must not stay stuck selected.
export const beginBulkDelete = (
  roster: ConversationRoster,
  ids: Iterable<ConversationId>,
): Result<ConversationRoster, BulkDeleteInFlight> =>
  roster.pendingBulkDelete !== null
    ? err({ kind: 'bulk-delete-in-flight' })
    : ok({ ...roster, pendingBulkDelete: new Set(ids) })

// FR-CONV-019: an error frame releases the guard without touching the roster, so a failed bulk
// delete does not block every later one.
export const releaseBulkDelete = (roster: ConversationRoster): ConversationRoster => ({
  ...roster,
  pendingBulkDelete: null,
})

// FR-CONV-006a. The server may report fewer deletions than were asked for; the survivors stay
// listed, the whole requested set leaves the selection, and the guard is released.
export const completeBulkDelete = (
  roster: ConversationRoster,
  deletedIds: readonly ConversationId[],
): ConversationRoster => {
  const requested = roster.pendingBulkDelete ?? new Set(deletedIds)
  const afterDelete = removeMany(roster, deletedIds)
  const selection = new Set([...afterDelete.selection].filter((id) => !requested.has(id)))

  return withSelection({ ...afterDelete, pendingBulkDelete: null }, selection)
}
