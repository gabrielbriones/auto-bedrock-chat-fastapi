import type { UsagePurpose } from '@/shared/ws/usage-frames'

// FR-DS-011 / NFR-I18N-001 — every user-facing `messaging` string is a typed constant here, never
// inline in JSX. Phase 3 covers the connection badge, the composer and a naive transcript only;
// streaming, tool activity and citation copy arrive with SPEC-012's Phase 4 requirements.
export const MESSAGING_COPY = {
  // FR-MSG-002
  connection: {
    label: 'Connection status',
    connected: 'Connected',
    connecting: 'Connecting...',
    disconnected: 'Disconnected',
  },

  transcript: {
    label: 'Transcript',
    user: 'You',
    assistant: 'Assistant',
    system: 'System',
    sending: 'Sending…',
    waiting: 'AI is typing...',
    thinking: 'Thinking...',
    truncated: 'Earlier context was truncated before this response.',
    sources: 'Sources',
    source: (position: number) => `Source ${position}`,
    toolCalls: 'Tool calls',
    toolRunning: 'Running',
    toolRan: 'Ran',
    toolFailed: 'Failed',
    toolInput: 'Input',
    toolOutput: 'Output',
    toolOutputPending: 'Output is available once the answer arrives.',
    toolsUsed: (count: number, failed: number) =>
      `Ran ${count} tools${failed > 0 ? ` (${failed} failed)` : ''}`,
    toolError: 'Error',
    failed: 'Failed',
    unknownTool: 'Unknown tool',
    toolResult: (position: number) => `Tool result ${position}`,
    copyCode: 'Copy code',
    interrupted: 'The connection dropped before this answer finished. Send the message again to retry.',
    interruptedExecution:
      'This request was interrupted in the middle of its execution and did not finish. Send the message again to retry.',
    responding: 'Assistant is responding',
    jumpToLatest: 'Jump to latest',
    jumpToLatestUnread: (count: number) => `Jump to latest, ${count} unread`,
    unreadCount: (count: number) => `${count}`,
  },

  usage: {
    tokens: (input: string, output: string) => `Tokens ${input} in / ${output} out`,
    estimated: (amount: string) => `Estimated ${amount}`,
    withPending: (label: string) => `${label} · pending`,
    inOut: (input: string, output: string) => `${input} in / ${output} out`,
    purposes: {
      chat: 'Chat',
      summarization: 'Summarization',
      retry: 'Retries',
      title: 'Title',
      embedding: 'Embedding',
    } satisfies Readonly<Record<UsagePurpose, string>>,
    cacheRead: 'Cache read',
    cacheWrite: 'Cache write',
    total: 'Total',
    unpriced: 'No price estimate for this model',
    pendingDetail: 'Final total pending',
    // Screen readers get the whole breakdown, since the tooltip is not announced.
    readTokens: (input: string, output: string) => `${input} input tokens, ${output} output tokens`,
    readPurpose: (purpose: string, input: string, output: string) =>
      `${purpose}: ${input} input, ${output} output`,
    readCache: (read: string, write: string) => `Cache read ${read} tokens, cache write ${write} tokens`,
    readAll: (parts: readonly string[]) => `Token usage. ${parts.join('. ')}.`,
  },

  // The open conversation's running totals and how full the model's input window is.
  session: {
    title: 'This conversation',
    noEstimate: 'No price estimate',
    tokens: (amount: string) => `${amount} tokens`,
    contextPercent: (percent: string) => `${percent} context`,
    answers: 'Answers',
    unpriced: (count: number) => `${count} ${count === 1 ? 'answer has' : 'answers have'} no price and ${count === 1 ? 'is' : 'are'} not included`,
    context: 'Context window',
    contextUsed: (used: string, max: string) => `${used} of ${max} tokens`,
    contextMeter: 'Context window used',
    read: (parts: readonly string[]) => `Conversation usage. ${parts.join('. ')}.`,
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
    placeholder: 'Type your message',
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
