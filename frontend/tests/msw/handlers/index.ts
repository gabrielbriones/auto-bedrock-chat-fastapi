import { bootstrapHandlers } from './bootstrap'
import { iamHandlers } from './iam'
import { knowledgeHandlers } from './knowledge'
import { reviewHandlers } from './review'
import { telemetryHandlers } from './telemetry'

// A new context adds its own file here (e.g. `handlers/conversation.ts`) and
// appends its array below — nothing else in the harness needs to change.
export const handlers = [
  ...bootstrapHandlers,
  ...iamHandlers,
  ...reviewHandlers,
  ...knowledgeHandlers,
  ...telemetryHandlers,
]
