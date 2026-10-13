import type { Schema } from 'hast-util-sanitize';

/** NFR-SEC-001: the only element allow-list for remotely supplied Markdown. */
export const MARKDOWN_ALLOWED_ELEMENTS = Object.freeze([
  'a',
  'blockquote',
  'br',
  'code',
  'del',
  'details',
  'div',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'img',
  'input',
  'kbd',
  'li',
  'ol',
  'p',
  'pre',
  's',
  'span',
  'strong',
  'sub',
  'summary',
  'sup',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'tr',
  'ul',
]);

/** NFR-SEC-002: URL schemes allowed by the shared Markdown pipeline. */
export const MARKDOWN_URL_SCHEMES = Object.freeze({
  href: Object.freeze(['http', 'https', 'mailto']),
  src: Object.freeze(['http', 'https', 'data']),
});

/** NFR-SEC-001: this schema is the single sanitisation policy for all Markdown consumers. */
export const MARKDOWN_SANITIZE_SCHEMA: Schema = {
  tagNames: [...MARKDOWN_ALLOWED_ELEMENTS],
  attributes: {
    a: ['href', 'title'],
    code: [['className', /^language-/]],
    img: ['alt', 'height', 'src', 'title', 'width'],
    input: [['checked', true], ['disabled', true], ['type', 'checkbox']],
    ol: [['className', 'contains-task-list']],
    li: [['className', 'task-list-item']],
    table: ['align'],
    td: ['align', 'colSpan', 'rowSpan'],
    th: ['align', 'colSpan', 'rowSpan'],
    '*': ['dir', 'lang', 'title'],
  },
  protocols: {
    href: [...MARKDOWN_URL_SCHEMES.href],
    src: [...MARKDOWN_URL_SCHEMES.src],
  },
  required: {
    input: { disabled: true, type: 'checkbox' },
  },
  strip: ['embed', 'form', 'iframe', 'object', 'script', 'style'],
};