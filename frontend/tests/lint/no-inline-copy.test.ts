import { RuleTester } from 'eslint'
import { describe, expect, it } from '@jest/globals'
import tseslint from 'typescript-eslint'

import rule from '../../eslint/rules/no-inline-copy.js'

// RuleTester drives its own `describe`/`it`; point them at Jest's.
RuleTester.describe = describe
RuleTester.it = it
RuleTester.itOnly = it.only

const ruleTester = new RuleTester({
  languageOptions: {
    parser: tseslint.parser,
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
})

ruleTester.run('no-inline-copy', rule, {
  valid: [
    { code: '<p>{COPY.title}</p>' },
    { code: '<p>{`${count} left`}</p>' },
    // Two words or fewer read unambiguously at the call site.
    { code: '<button>Log out</button>' },
    { code: '<span>Skip</span>' },
    // The documented allow-list: symbols, punctuation and separators carry no prose.
    { code: '<span>·</span>' },
    { code: '<span>— / — · 12 · 3.4 %</span>' },
    { code: '<span>{" "}</span>' },
    // Explicit allow-list entries.
    { code: '<h1>Intel Workload Analyzer</h1>', options: [{ allow: ['Intel Workload Analyzer'] }] },
    // Attributes are out of scope: only rendered text is matched.
    { code: '<input placeholder="Enter your JWT or OAuth token" />' },
  ],
  invalid: [
    {
      code: '<p>Something went wrong here</p>',
      errors: [{ messageId: 'inlineCopy' }],
    },
    {
      code: '<p>{"Something went wrong here"}</p>',
      errors: [{ messageId: 'inlineCopy' }],
    },
    {
      // Whitespace and line breaks are normalised before counting.
      code: '<p>\n  Session expired.\n  Please log in again.\n</p>',
      errors: [{ messageId: 'inlineCopy' }],
    },
    {
      code: '<h1>Intel Workload Analyzer</h1>',
      options: [{ allow: ['Something else'] }],
      errors: [{ messageId: 'inlineCopy' }],
    },
  ],
})

describe('the allow-list', () => {
  it('is documented on the rule so the exceptions stay reviewable', () => {
    expect(rule.meta?.schema).toBeDefined()
  })
})
