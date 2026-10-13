import noInlineCopy from './rules/no-inline-copy.js'

/**
 * In-repo rules that back STD-001 §4 where no published plugin covers the requirement.
 *
 * @type {import('eslint').ESLint.Plugin}
 */
const architecture = {
  meta: { name: 'architecture' },
  rules: {
    'no-inline-copy': noInlineCopy,
  },
}

export default architecture
