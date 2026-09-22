// FR-DS-011 / NFR-I18N-001 — every user-facing `messaging` string is a typed constant here, never
// inline in JSX. Phase 3 covers the connection badge, the composer and a naive transcript only;
// streaming, tool activity and citation copy arrive with SPEC-012's Phase 4 requirements.
export const MESSAGING_COPY = {
  // FR-MSG-002
  connection: {
    label: 'Connection status',
    connected: 'Connected',
    connecting: 'Connecting…',
    disconnected: 'Disconnected',
  },

  transcript: {
    label: 'Transcript',
    user: 'You',
    assistant: 'Assistant',
    system: 'System',
    sending: 'Sending…',
    waiting: 'AI is typing...',
    truncated: 'Earlier context was truncated before this response.',
    sources: 'Sources',
    source: (position: number) => `Source ${position}`,
    toolActivity: (names: string) => `Tool activity: ${names}`,
    toolResults: 'tool results',
    arguments: 'Arguments',
    result: 'Result',
    toolError: 'Error',
    status: 'Status',
    completed: 'Completed',
    failed: 'Failed',
    unknownTool: 'Unknown tool',
    toolResult: (position: number) => `Tool result ${position}`,
    copyCode: 'Copy code',
    interrupted: 'The connection dropped before this answer finished. Send the message again to retry.',
    responding: 'Assistant is responding',
    jumpToLatest: 'Jump to latest',
    jumpToLatestUnread: (count: number) => `Jump to latest, ${count} unread`,
    unreadCount: (count: number) => `${count}`,
  },

  welcome: {
    label: 'Welcome',
    suggestionsLabel: 'Suggested prompts',
    suggestions: [
      'Summarise my most recent workload analysis',
      'Which functions dominate CPU time in this run?',
      'Explain the top bottleneck and what to try next',
    ],
  },

  composer: {
    label: 'Message',
    placeholder: 'Type your message...',
    waitingPlaceholder: 'Waiting for response...',
    send: 'Send',
    hint: 'Enter to send, Shift+Enter for a new line',
    // FR-MSG-012/012a: a disabled composer always states why, rather than being inertly greyed out.
    disabled: {
      unauthenticated: 'Sign in to send a message.',
      offline: 'Disconnected — you can send again once the connection is back.',
      responding: 'Waiting for the assistant to finish responding.',
    },
  },

  errors: {
    dropped: 'The message could not be sent because the connection is closed.',
    unknown: 'The assistant could not answer that request.',
  },
} as const
