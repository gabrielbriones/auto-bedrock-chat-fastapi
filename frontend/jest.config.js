// `tests/` mirrors `src/` per bounded context, alongside four suites that belong to no context:
// `e2e/` (selenium, never jsdom), `msw/`, `lint/` and `setup/`. The context mirrors are what
// `unit` and `component` claim, so those four are subtracted from them here.
const NON_CONTEXT_SUITES = ['<rootDir>/tests/e2e/', '<rootDir>/tests/msw/', '<rootDir>/tests/lint/', '<rootDir>/tests/setup/']

// The package is `"type": "module"` and `eslint/**/*.js` is imported by the lint suite, so Jest
// runs in native ESM mode (`--experimental-vm-modules`, see the npm scripts) rather than
// transpiling to CommonJS.
const shared = {
  rootDir: import.meta.dirname,
  extensionsToTreatAsEsm: ['.ts', '.tsx'],
  setupFilesAfterEnv: ['<rootDir>/tests/setup/matchers.ts'],
  transform: {
    '\\.[jt]sx?$': '<rootDir>/jest/swc-transformer.mjs',
    '\\.html$': '<rootDir>/jest/raw-transformer.mjs',
  },
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    // ESM-style `./helper.js` specifiers pointing at `.ts` sources.
    '^(\\.{1,2}/.*)\\.js$': '$1',
    '^(.+)\\?raw$': '$1',
    '\\.css$': '<rootDir>/jest/style-mock.js',
  },
}

const project = (name, overrides) => ({ ...shared, displayName: name, ...overrides })

const jsdomProject = (name, overrides) =>
  project(name, {
    testEnvironment: '<rootDir>/jest/jsdom-environment.mjs',
    setupFilesAfterEnv: [...shared.setupFilesAfterEnv, '<rootDir>/tests/setup/jsdom.setup.ts'],
    ...overrides,
  })

/** @type {import('jest').Config} */
export default {
  testTimeout: 60_000,
  projects: [
    project('unit', {
      testEnvironment: 'node',
      testMatch: ['<rootDir>/tests/**/*.test.ts'],
      testPathIgnorePatterns: ['/node_modules/', ...NON_CONTEXT_SUITES],
    }),
    jsdomProject('component', {
      testMatch: ['<rootDir>/tests/**/*.test.tsx'],
      testPathIgnorePatterns: ['/node_modules/', ...NON_CONTEXT_SUITES],
    }),
    project('contract', {
      testEnvironment: 'node',
      testMatch: ['<rootDir>/tests/msw/**/*.contract.test.ts'],
    }),
    project('lint', {
      testEnvironment: 'node',
      testMatch: ['<rootDir>/tests/lint/*.test.ts'],
    }),
    jsdomProject('bench', {
      testMatch: ['<rootDir>/tests/**/*.bench.tsx'],
    }),
    project('e2e', {
      testEnvironment: 'node',
      testMatch: ['<rootDir>/tests/e2e/**/*.test.ts'],
    }),
  ],
  coverageProvider: 'v8',
  coverageReporters: ['text', 'json', 'html'],
  coverageDirectory: '<rootDir>/coverage',
  coveragePathIgnorePatterns: [
    '/node_modules/',
    '/dist/',
    '<rootDir>/src/routeTree.gen.ts',
    '<rootDir>/src/main.tsx',
    '<rootDir>/src/vite-env.d.ts',
    '\\.config\\.',
    '<rootDir>/tests/',
    '<rootDir>/jest/',
  ],
  coverageThreshold: {
    global: {
      statements: 85,
      branches: 80,
      functions: 80,
      lines: 85,
    },
    'src/shared/**': {
      statements: 85,
      branches: 80,
    },
    'src/domains/*/domain/**': {
      statements: 95,
      branches: 90,
    },
    'src/domains/*/application/**': {
      statements: 85,
      branches: 80,
    },
    'src/domains/*/infrastructure/**': {
      statements: 90,
      branches: 85,
    },
    'src/domains/*/presentation/**': {
      statements: 70,
    },
  },
}
