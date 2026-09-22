import { HttpResponse, http } from 'msw'

import feedbackEntry from '../fixtures/review/feedback-entry.json'
import feedbackList from '../fixtures/review/feedback-list.json'
import feedbackStats from '../fixtures/review/feedback-stats.json'
import rollbackResult from '../fixtures/review/rollback-result.json'
import synthesisResult from '../fixtures/review/synthesis-result.json'
import synthesisStatus from '../fixtures/review/synthesis-status.json'

// CT-2: every REST endpoint of SPEC-016 §3 has a handler here and a fixture beside it, and
// `review.contract.test.ts` parses each fixture through the production schema. The base path
// matches the deployed `ADMIN` prefix of CONTRACT-001 §1.
const ADMIN = '*/bedrock-chat/admin'

export const reviewHandlers = [
  http.get(`${ADMIN}/feedback/stats`, () => HttpResponse.json(feedbackStats)),
  http.get(`${ADMIN}/feedback/:id`, () => HttpResponse.json(feedbackEntry)),
  http.get(`${ADMIN}/feedback`, () => HttpResponse.json(feedbackList)),
  http.patch(`${ADMIN}/feedback/:id`, () => HttpResponse.json(feedbackEntry)),
  http.delete(`${ADMIN}/feedback/:id`, () => new HttpResponse(null, { status: 204 })),
  http.get(`${ADMIN}/synthesis/status`, () => HttpResponse.json(synthesisStatus)),
  http.post(`${ADMIN}/synthesis/trigger/:id`, () => HttpResponse.json(synthesisResult)),
  http.post(`${ADMIN}/synthesis/rollback/:id`, () => HttpResponse.json(rollbackResult)),
]

// I1 (DESIGN-001 §6.1): the server refuses to delete anything that is not rejected. Install with
// `server.use(...)` to drive the conflict path.
export const feedbackDeleteConflictHandler = http.delete(`${ADMIN}/feedback/:id`, () =>
  HttpResponse.json(
    { code: 'invalid_state', detail: "only feedback in the 'rejected' state may be deleted" },
    { status: 409 },
  ),
)

// FR-REV-016 / BC-007: a batch run in progress must disable the per-entry action.
export const synthesisRunningHandler = http.get(`${ADMIN}/synthesis/status`, () =>
  HttpResponse.json({ ...synthesisStatus, phase: 'running', finished_at: null }),
)
