import { HttpResponse, http } from 'msw'

import byDay from '../fixtures/telemetry/token-by-day.json' with { type: 'json' }
import byUser from '../fixtures/telemetry/token-by-user.json' with { type: 'json' }
import summary from '../fixtures/telemetry/token-summary.json' with { type: 'json' }
import topUsers from '../fixtures/telemetry/token-top-users.json' with { type: 'json' }

const ADMIN = '*/bedrock-chat/admin'

export const telemetryHandlers = [
  http.get(`${ADMIN}/tokens/summary`, () => HttpResponse.json(summary)),
  http.get(`${ADMIN}/tokens/top-users`, () => HttpResponse.json(topUsers)),
  http.get(`${ADMIN}/tokens/by-day`, () => HttpResponse.json(byDay)),
  http.get(`${ADMIN}/tokens/by-user`, () => HttpResponse.json(byUser)),
]

export const invalidDateRangeHandler = http.get(`${ADMIN}/tokens/by-day`, () =>
  HttpResponse.json(
    { code: 'invalid_date_range', detail: 'end must be after start' },
    { status: 400 },
  ),
)