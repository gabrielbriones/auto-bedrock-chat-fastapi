import { setupServer } from 'msw/node'

import { handlers } from './handlers'

// Shared node-side MSW server for unit/component/integration tests.
export const server = setupServer(...handlers)
