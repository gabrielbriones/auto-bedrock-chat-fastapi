import path from 'node:path'

import js from '@eslint/js'
import { defineConfig } from 'eslint/config'
import boundaries from 'eslint-plugin-boundaries'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

import architecture from './plugin.js'

const ALIAS_RESOLVER = path.join(import.meta.dirname, 'alias-resolver.cjs')
const ROOT_DIR = path.join(import.meta.dirname, '..')

// DESIGN-002 §1: the four layers every bounded context is sliced into.
const CONTEXT_LAYERS = ['domain', 'application', 'infrastructure', 'presentation']

// STD-001 §4 FR-TOOL-010: packages that would drag a framework, a store or a schema library into
// the pure layer. `domain` must stay runnable in a bare Node test runner.
const PACKAGES_BANNED_IN_DOMAIN = ['react', 'react-dom', '@tanstack/*', 'zustand', 'zod']

// FR-TOOL-019: the only modules allowed to touch URL state directly. Everywhere else navigates
// through `validateSearch` and the typed targets in `src/app/route-links.ts`.
const URL_STATE_OWNERS = [
  'src/routes/**/*.{ts,tsx}',
  'src/app/browser-location.ts',
  'src/app/router.tsx',
  'src/app/route-links.ts',
  'src/app/search-params.ts',
]

const TEST_FILES = ['tests/**/*.{ts,tsx}']

// FIX-15 / FR-TOOL-012.
const NATIVE_DIALOGS = {
  globals: ['alert', 'confirm', 'prompt'].map((name) => ({
    name,
    message: 'Native dialogs are banned (FR-TOOL-012). Use the confirmation or notification port.',
  })),
  syntax: [
    {
      selector: 'MemberExpression[object.name="window"][property.name=/^(alert|confirm|prompt)$/]',
      message: 'Native dialogs are banned (FR-TOOL-012). Use the confirmation or notification port.',
    },
  ],
}

// FR-TOOL-013.
const RAW_HTML = [
  {
    selector: 'JSXAttribute[name.name="dangerouslySetInnerHTML"]',
    message: 'Raw HTML injection is banned outside src/shared/markdown/ (FR-TOOL-013).',
  },
  {
    selector: 'Property[key.name="dangerouslySetInnerHTML"]',
    message: 'Raw HTML injection is banned outside src/shared/markdown/ (FR-TOOL-013).',
  },
]

// FR-TOOL-019.
const URL_STATE = {
  globals: [
    {
      name: 'URLSearchParams',
      message:
        'Search state is typed: use validateSearch and typed navigation instead (FR-TOOL-019).',
    },
  ],
  syntax: [
    {
      selector: 'MemberExpression[object.property.name="location"][property.name="search"]',
      message:
        'Search state is typed: use validateSearch and typed navigation instead (FR-TOOL-019).',
    },
    {
      selector: 'MemberExpression[property.name=/^(pushState|replaceState)$/]',
      message: 'History is owned by the router: navigate through it instead (FR-TOOL-019).',
    },
  ],
}

// FR-TOOL-015 / SPEC-020 §7: contexts consume semantic tokens only — never a hex/functional colour
// and never a raw palette ramp, which would break theming.
const COLOUR_MESSAGE =
  'Colour literals and palette tokens are banned in src/domains: use a semantic token (FR-TOOL-015).'
const COLOUR_LITERALS = [
  'Literal[value=/#[0-9a-fA-F]{3,8}\\b/]',
  'Literal[value=/(rgb|rgba|hsl|hsla|oklch|lab|lch|color-mix)\\(/]',
  'Literal[value=/palette-/]',
  'TemplateElement[value.raw=/#[0-9a-fA-F]{3,8}\\b/]',
  'TemplateElement[value.raw=/(rgb|rgba|hsl|hsla|oklch|lab|lch|color-mix)\\(/]',
  'TemplateElement[value.raw=/palette-/]',
].map((selector) => ({ selector, message: COLOUR_MESSAGE }))

/**
 * The rules of STD-001 §4 that enforce `DESIGN-002`'s layering and `CONST`'s bounded-context
 * isolation.
 *
 * @returns {import('eslint').Linter.Config[]}
 */
export function architectureConfig() {
  return defineConfig([
    {
      files: ['**/*.{ts,tsx}'],
      extends: [
        js.configs.recommended,
        tseslint.configs.recommended,
        reactHooks.configs.flat.recommended,
        reactRefresh.configs.vite,
      ],
      plugins: { boundaries, architecture },
      languageOptions: {
        globals: globals.browser,
      },
      settings: {
        'boundaries/root-path': ROOT_DIR,
        // `@/` is a tsconfig path alias, which the plugin's resolver does not know about on its
        // own — without this every project import looks like an external package.
        'import/resolver': {
          [ALIAS_RESOLVER]: { srcDir: path.join(ROOT_DIR, 'src') },
          node: { extensions: ['.ts', '.tsx', '.js', '.jsx', '.json'] },
        },
        'boundaries/legacy-templates': false,
        'boundaries/files': [{ pattern: '**/*.{test,type-test,fixture}.{ts,tsx}', category: 'test' }],
        // STD-002 §1: `tests/` mirrors `src/` path-for-path — `src/domains/<ctx>/<layer>` is
        // `tests/domains/<ctx>/<layer>`. Each mirror is declared alongside its subject so a spec is
        // classified into the same element, and captures the same context, as the code it covers.
        // Without that a spec would resolve to no element at all and every policy below would
        // silently stop applying to it.
        'boundaries/elements': [
          {
            type: 'domain',
            pattern: ['src/domains/*/domain', 'tests/domains/*/domain'],
            partialMatch: false,
            capture: ['context'],
          },
          {
            type: 'application',
            pattern: ['src/domains/*/application', 'tests/domains/*/application'],
            partialMatch: false,
            capture: ['context'],
          },
          {
            type: 'infrastructure',
            pattern: ['src/domains/*/infrastructure', 'tests/domains/*/infrastructure'],
            partialMatch: false,
            capture: ['context'],
          },
          {
            type: 'presentation',
            pattern: ['src/domains/*/presentation', 'tests/domains/*/presentation'],
            partialMatch: false,
            capture: ['context'],
          },
          { type: 'shared', pattern: ['src/shared', 'tests/shared'], partialMatch: false },
          { type: 'app', pattern: ['src/app', 'tests/app'], partialMatch: false },
          { type: 'routes', pattern: 'src/routes', partialMatch: false },
          // ADR-015: the vendored shadcn layer, which only `presentation` may reach.
          { type: 'ui', pattern: ['src/components', 'tests/components'], partialMatch: false },
          { type: 'lib', pattern: 'src/lib', partialMatch: false },
        ],
      },
      rules: {
        'boundaries/dependencies': [
          'error',
          {
            default: 'allow',
            // FR-TOOL-010 bans packages, not just modules, and the rule skips anything but local
            // imports unless asked.
            checkAllOrigins: true,
            policies: [
              {
                from: { element: { type: 'domain' } },
                disallow: {
                  to: [
                    {
                      element: {
                        types: {
                          anyOf: [
                            'application',
                            'infrastructure',
                            'presentation',
                            'app',
                            'routes',
                            'ui',
                            'lib',
                          ],
                        },
                      },
                    },
                    { module: { origin: 'external', source: PACKAGES_BANNED_IN_DOMAIN } },
                  ],
                },
                message:
                  'domain is pure (FR-TOOL-010): it may only import shared/kernel and other contexts’ domain/public.ts.',
              },
              {
                from: { element: { type: 'application' } },
                disallow: {
                  to: { element: { types: { anyOf: ['infrastructure', 'presentation'] } } },
                },
                message:
                  'application declares ports; adapters and components depend on it, never the other way round (FR-TOOL-010).',
              },
              {
                from: { element: { type: 'presentation' } },
                disallow: { to: { element: { type: 'infrastructure' } } },
                message:
                  'presentation reaches infrastructure through application ports, resolved by the composition root (FR-TOOL-010).',
              },
              {
                from: {
                  element: { types: { anyOf: ['domain', 'application', 'infrastructure'] } },
                },
                disallow: { to: { element: { types: { anyOf: ['ui', 'lib'] } } } },
                message: 'Only presentation may import the vendored UI layer (ADR-015).',
              },
              {
                // A spec composes the layers it exercises, the way the composition root does; what it
                // must not do is reach into another context, which the next policy re-bans.
                from: { file: { categories: 'test' } },
                allow: {
                  to: {
                    element: {
                      types: { anyOf: CONTEXT_LAYERS },
                      captured: { context: '{{ from.element.captured.context }}' },
                    },
                  },
                },
              },
              {
                from: { element: { types: { anyOf: CONTEXT_LAYERS } } },
                disallow: {
                  to: {
                    element: {
                      types: { anyOf: CONTEXT_LAYERS },
                      captured: { context: '!{{ from.element.captured.context }}' },
                    },
                  },
                },
                message:
                  'Bounded contexts are isolated: import {{ to.element.captured.context }} through its domain/public.ts (FR-TOOL-011).',
              },
              {
                // …which is the one module of another context that is importable.
                from: { element: { types: { anyOf: CONTEXT_LAYERS } } },
                allow: { to: { element: { type: 'domain', fileInternalPath: 'public.ts' } } },
              },
            ],
          },
        ],

        // NFR-OBS-004 / FR-TOOL-014.
        'no-console': 'error',

        'no-restricted-globals': ['error', ...NATIVE_DIALOGS.globals, ...URL_STATE.globals],
        'no-restricted-syntax': [
          'error',
          ...NATIVE_DIALOGS.syntax,
          ...RAW_HTML,
          ...URL_STATE.syntax,
        ],

        // NFR-MAINT-003 / FR-TOOL-017.
        'max-lines': ['error', { max: 300, skipBlankLines: true, skipComments: true }],
        'max-lines-per-function': [
          'error',
          { max: 50, skipBlankLines: true, skipComments: true, IIFEs: true },
        ],

        // FR-TOOL-018.
        '@typescript-eslint/no-non-null-assertion': 'error',
      },
    },

    {
      // STD-001 §6: every module under `src` is reached through the `@/` alias, so a file's
      // imports read the same wherever it is moved to. `src` now contains production code only,
      // so it has nothing left to reach relatively.
      files: ['src/**/*.{ts,tsx}'],
      rules: {
        'no-restricted-imports': [
          'error',
          {
            patterns: [
              {
                regex: String.raw`^\.\.?/`,
                message: 'Import project modules through the @/ alias (STD-001 §6).',
              },
            ],
          },
        ],
      },
    },

    {
      // The mirror image of the rule above. Inside `tests` a sibling spec, fixture or helper is a
      // relative import — the alias cannot address them — but production code is still reached
      // through `@/`, so a spec never encodes how deep its own mirror happens to sit.
      files: ['tests/**/*.{ts,tsx}'],
      rules: {
        'no-restricted-imports': [
          'error',
          {
            patterns: [
              {
                regex: String.raw`^\.\.?/(\.\./)*src/`,
                message: 'Reach production modules through the @/ alias (STD-001 §6).',
              },
            ],
          },
        ],
      },
    },

    {
      // FR-TOOL-018's exhaustiveness check needs type information, as does the no-floating-promises
      // rule STD-001 §6 assumes. Both trees have a TS project every file resolves to:
      // `tsconfig.app.json` for `src`, `tests/tsconfig.json` for `tests`.
      files: ['src/**/*.{ts,tsx}', 'tests/**/*.{ts,tsx}'],
      languageOptions: {
        parserOptions: {
          projectService: true,
          tsconfigRootDir: ROOT_DIR,
        },
      },
      rules: {
        '@typescript-eslint/switch-exhaustiveness-check': [
          'error',
          { considerDefaultExhaustiveForUnions: true },
        ],
        '@typescript-eslint/no-floating-promises': 'error',
      },
    },

    {
      // FR-TOOL-016: the one place a long user-facing string may be written as a literal is the
      // copy module itself; everywhere else JSX reads it from there.
      files: ['src/**/*.tsx'],
      rules: {
        'architecture/no-inline-copy': [
          'error',
          {
            // Allow-list: strings that are not translatable prose. Keep it short and justified.
            allow: ['Intel Workload Analyzer'],
          },
        ],
      },
    },

    {
      // FR-TOOL-014: the logging adapter is the sink every other module writes through.
      files: ['src/shared/logging/**/*.ts'],
      rules: { 'no-console': 'off' },
    },

    {
      // FR-TOOL-013: the sanitized Markdown renderer is the single audited injection point.
      files: ['src/shared/markdown/**/*.{ts,tsx}'],
      rules: {
        'no-restricted-syntax': ['error', ...NATIVE_DIALOGS.syntax, ...URL_STATE.syntax],
      },
    },

    {
      files: URL_STATE_OWNERS,
      rules: {
        'no-restricted-globals': ['error', ...NATIVE_DIALOGS.globals],
        'no-restricted-syntax': ['error', ...NATIVE_DIALOGS.syntax, ...RAW_HTML],
      },
    },

    {
      files: ['src/domains/**/*.{ts,tsx}'],
      rules: {
        'no-restricted-syntax': [
          'error',
          ...NATIVE_DIALOGS.syntax,
          ...RAW_HTML,
          ...URL_STATE.syntax,
          ...COLOUR_LITERALS,
        ],
      },
    },

    {
      // Vendored shadcn primitives (ADR-011): cva variants are intentionally co-exported with each
      // component, which trips react-refresh's export check. Route files (DESIGN-002 §2.2)
      // co-export their Route object and search schema alongside the component for the same reason.
      files: ['src/components/ui/**/*.{ts,tsx}', 'src/routes/**/*.{ts,tsx}'],
      rules: {
        'react-refresh/only-export-components': 'off',
      },
    },

    {
      // FR-TOOL-017 sizes a module against how much behaviour a reader must hold at once; a spec
      // file is read one case at a time. FR-TOOL-018 allows `!` in tests, where a failed assumption
      // fails the test rather than a user's session.
      files: TEST_FILES,
      rules: {
        'max-lines': 'off',
        'max-lines-per-function': 'off',
        '@typescript-eslint/no-non-null-assertion': 'off',
      },
    },
  ])
}
