/**
 * FR-TOOL-016 / NFR-I18N-001 — user-facing text may not be written inline in JSX.
 *
 * Any JSX text node (or string literal rendered as a child) of more than two words must come from
 * a typed constant in `src/shared/copy/`. Text with two words or fewer, and text made only of
 * symbols and punctuation, is allowed: it is either a label short enough to be unambiguous at the
 * call site or not translatable at all. Anything else that must stay inline goes in the rule's
 * `allow` option, which is the documented allow-list.
 *
 * @type {import('eslint').Rule.RuleModule}
 */
const rule = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Require user-facing JSX text longer than two words to come from src/shared/copy/',
    },
    schema: [
      {
        type: 'object',
        properties: {
          allow: {
            type: 'array',
            items: { type: 'string' },
            uniqueItems: true,
          },
        },
        additionalProperties: false,
      },
    ],
    messages: {
      inlineCopy:
        'Inline user-facing text "{{text}}" — move it to a typed constant in src/shared/copy/ (FR-TOOL-016).',
    },
  },

  create(context) {
    const { allow = [] } = context.options[0] ?? {}
    const allowed = new Set(allow.map(normalize))

    /**
     * @param {string} raw
     * @param {import('estree').Node} node
     */
    function check(raw, node) {
      const text = normalize(raw)
      if (text === '' || allowed.has(text) || countWords(text) <= 2) return

      context.report({ node, messageId: 'inlineCopy', data: { text } })
    }

    return {
      JSXText(node) {
        check(node.value, node)
      },

      // `{'some long sentence'}` is the same thing written as an expression container.
      JSXExpressionContainer(node) {
        const { expression, parent } = node
        if (parent.type !== 'JSXElement' && parent.type !== 'JSXFragment') return
        if (expression.type !== 'Literal' || typeof expression.value !== 'string') return

        check(expression.value, expression)
      },
    }
  },
}

/** @param {string} text */
function normalize(text) {
  return text.replace(/\s+/g, ' ').trim()
}

// A "word" is a token carrying at least one letter, so `—`, `·`, `1.`, `%` and friends never count.
/** @param {string} text */
function countWords(text) {
  return text.split(' ').filter((token) => /\p{L}/u.test(token)).length
}

export default rule
