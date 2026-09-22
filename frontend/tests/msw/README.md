# tests/msw — shared network mocking

One MSW handler per backend endpoint or WebSocket frame described in `specs/contracts/001-backend-api.md`,
shared by unit, component, integration and contract tests (`STD-002` §4).

## Adding a handler for a new context

1. Create `handlers/<context>.ts` exporting an array of `http.*`/`ws.*` handlers, one per endpoint
   the context calls.
2. Append that array to `handlers` in [`handlers/index.ts`](handlers/index.ts). Nothing else needs
   to change — `server.ts` and every test that imports it picks the new handlers up automatically.
3. Put any wire-shaped sample payload the handler returns in `fixtures/<context>/*.json`. Fixtures
   are shared between component/integration tests and the `contract` project, so a stale fixture is
   caught in both places.

## Usage in tests

```ts
import { server } from '../../tests/msw/server'
import { http, HttpResponse } from 'msw'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// Per-test overrides go through server.use(...); default handlers live in handlers/.
```

`onUnhandledRequest: 'error'` is required everywhere — an un-mocked request is a test bug, not a
pass-through.

## Contract project

`npm run test:contract` runs `tests/msw/**/*.contract.test.ts` (see `vitest.config.ts`). A contract
test asserts a handler's response against the same schema/shape production code expects, so a fixture
drifting from the real backend fails here first (`CT-1`, `CT-2`).
