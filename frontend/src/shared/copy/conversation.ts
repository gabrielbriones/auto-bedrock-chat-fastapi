// FR-DS-011 / NFR-I18N-001 — every user-facing `conversation` string is a typed constant here,
// never inline in JSX. Wording that SPEC-011 quotes verbatim is reproduced exactly.
export const CONVERSATION_COPY = {
  sidebar: {
    label: 'Conversations',
    newChat: 'New chat',
    open: 'Open conversations',
    close: 'Close conversations',
    empty: 'No conversations yet.',
    emptyHint: 'Ask something to start one.',
  },

  // FR-CONV-014.
  untitled: 'Untitled conversation',

  item: {
    select: (title: string) => `Select ${title}`,
    options: (title: string) => `Options for ${title}`,
    open: (title: string) => `Open ${title}`,
    rename: 'Rename',
    delete: 'Delete',
    messageCount: (count: number) => (count === 1 ? '1 message' : `${count} messages`),
  },

  // FR-CONV-004.
  rename: {
    title: 'Rename conversation',
    label: 'Title',
    confirm: 'Rename',
    empty: 'Enter a title.',
  },

  // FR-CONV-005: the confirmation names the conversation, so a mis-click is visible before it lands.
  delete: {
    title: 'Delete conversation',
    message: (title: string) => `Delete "${title}"? This cannot be undone.`,
    confirm: 'Delete',
  },

  // FR-CONV-006.
  bulk: {
    selectAll: 'Select all conversations',
    selected: (count: number) => `${count} selected`,
    action: 'Delete selected',
    clear: 'Clear selection',
    title: (count: number) =>
      count === 1 ? 'Delete 1 conversation?' : `Delete ${count} conversations?`,
    message: 'This cannot be undone.',
    confirm: 'Delete',
    inFlight: 'A delete is already running.',
    // P5: the server may report fewer deletions than were asked for.
    partial: (count: number) =>
      count === 1 ? '1 conversation could not be deleted.' : `${count} conversations could not be deleted.`,
  },

  // FR-CONV-017. The delete wording is quoted by the spec; the others follow its shape so the user
  // is told which mutation did not happen, not merely that something did not.
  offline: {
    delete: 'Not connected — conversations were not deleted.',
    rename: 'Not connected — the conversation was not renamed.',
    load: 'Not connected — the conversation was not opened.',
    create: 'Not connected — a new conversation was not started.',
    refresh: 'Not connected — the conversation list was not refreshed.',
  },

  // FR-CONV-019.
  errors: {
    notFound: 'That conversation no longer exists.',
    persistenceDisabled: 'Saved conversations are unavailable for this session.',
    invalidRequest: 'That conversation request was rejected.',
    transient: (message: string) => `⚠️ ${message}`,
  },

  // FR-CONV-009 / FR-CONV-009c.
  pending: {
    notice: 'Still working on a previous request...',
    exhausted: 'That request is still unfinished after two minutes.',
    retry: 'Check again',
  },

  // FR-MSG-008 / FR-CONV-010.
  unknown: {
    title: 'Conversation not found',
    description: 'That link does not match a saved conversation.',
    action: 'Start a new conversation',
  },
} as const
