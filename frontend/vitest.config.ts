import path from 'node:path'
import { configDefaults, defineConfig } from 'vitest/config'

// `tests/` mirrors `src/` per bounded context, alongside four suites that belong to no context:
// `e2e/` (mocha + selenium, never vitest), `msw/`, `lint/` and `setup/`. The context mirrors are
// what `unit` and `component` claim, so those four are subtracted from them here.
const NON_CONTEXT_SUITES = ['tests/e2e/**', 'tests/msw/**', 'tests/lint/**', 'tests/setup/**']

// STD-002 §1: unit/application code runs under node, components under jsdom,
// and the contract suite (network fixtures validated against real payloads) is
// its own project so `npm run test:contract` can be run in isolation in CI.
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  test: {
    projects: [
      {
        extends: true,
        test: {
          name: 'unit',
          environment: 'node',
          include: ['tests/**/*.test.ts'],
          exclude: [...configDefaults.exclude, ...NON_CONTEXT_SUITES],
        },
      },
      {
        extends: true,
        test: {
          name: 'component',
          environment: 'jsdom',
          include: ['tests/**/*.test.tsx'],
          exclude: [...configDefaults.exclude, ...NON_CONTEXT_SUITES],
          setupFiles: ['./tests/setup/jsdom.setup.ts'],
        },
      },
      {
        extends: true,
        test: {
          name: 'contract',
          environment: 'node',
          include: ['tests/msw/**/*.contract.test.ts'],
        },
      },
      {
        // The in-repo lint rule of STD-001 §4, driven by ESLint's RuleTester.
        extends: true,
        test: {
          name: 'lint',
          environment: 'node',
          include: ['tests/lint/*.test.ts'],
        },
      },
      {
        // T-084 / NFR-PERF-005. Its own project so a timing budget never runs inside the
        // coverage-instrumented suite, which would make the clock meaningless.
        extends: true,
        test: {
          name: 'bench',
          environment: 'jsdom',
          include: ['tests/**/*.bench.tsx'],
          setupFiles: ['./tests/setup/jsdom.setup.ts'],
        },
      },
    ],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      reportsDirectory: './coverage',
      exclude: [
        'node_modules/**',
        'dist/**',
        'src/routeTree.gen.ts',
        'src/main.tsx',
        'src/vite-env.d.ts',
        '**/*.config.*',
        'tests/**',
      ],
      thresholds: {
        statements: 85,
        branches: 80,
        functions: 80,
        lines: 85,
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
        'src/domains/knowledge/domain/**': {
          statements: 95,
          branches: 90,
        },
        'src/domains/knowledge/application/**': {
          statements: 85,
          branches: 80,
        },
        'src/domains/knowledge/infrastructure/**': {
          statements: 90,
          branches: 85,
        },
        'src/domains/knowledge/presentation/**': {
          statements: 70,
        },
      },
    },
  },
})
