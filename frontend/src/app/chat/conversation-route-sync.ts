import type { ConversationId } from '@/shared/kernel/branded'

export type ConversationRouteSyncState = {
  readonly previousActiveId: ConversationId | null
  readonly requestedRouteId: ConversationId | null
}

export type ConversationRouteSyncInput = ConversationRouteSyncState & {
  readonly routeId: ConversationId | null
  readonly activeId: ConversationId | null
  readonly available: boolean
  readonly awaitingId: boolean
}

export type ConversationRouteSyncCommand =
  | { readonly kind: 'none' }
  | { readonly kind: 'load'; readonly id: ConversationId }
  | { readonly kind: 'navigate'; readonly id: ConversationId | null }

export type ConversationRouteSyncResult = ConversationRouteSyncState & {
  readonly command: ConversationRouteSyncCommand
}

const result = (
  previousActiveId: ConversationId | null,
  requestedRouteId: ConversationId | null,
  command: ConversationRouteSyncCommand = { kind: 'none' },
): ConversationRouteSyncResult => ({ previousActiveId, requestedRouteId, command })

export const synchronizeConversationRoute = (
  input: ConversationRouteSyncInput,
): ConversationRouteSyncResult => {
  const activeChanged = input.activeId !== input.previousActiveId

  if (input.awaitingId) {
    const command = input.routeId === null ? undefined : { kind: 'navigate' as const, id: null }
    return result(input.activeId, null, command)
  }

  if (!input.available) {
    return result(input.activeId, null)
  }

  if (input.routeId === null) {
    const command = activeChanged && input.activeId !== null
      ? { kind: 'navigate' as const, id: input.activeId }
      : undefined
    return result(input.activeId, null, command)
  }

  if (input.activeId === input.routeId) {
    return result(input.activeId, null)
  }

  const externalSelection =
    activeChanged && input.activeId !== null && input.activeId !== input.requestedRouteId

  if (externalSelection) {
    return result(input.activeId, null, { kind: 'navigate', id: input.activeId })
  }

  if (input.requestedRouteId === input.routeId) {
    return result(input.activeId, input.requestedRouteId)
  }

  if (activeChanged && input.activeId !== null) {
    return result(input.activeId, null, { kind: 'navigate', id: input.activeId })
  }

  return result(input.activeId, input.routeId, { kind: 'load', id: input.routeId })
}
