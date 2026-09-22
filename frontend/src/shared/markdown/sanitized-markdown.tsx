import type { ComponentType, ReactNode } from 'react';
import type { Components, Options as MarkdownOptions, UrlTransform } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize from 'rehype-sanitize';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';

import { cn } from '@/lib/utils';
import { MARKDOWN_SANITIZE_SCHEMA } from '@/shared/markdown/sanitize-schema';

type CodeBlockComponent = ComponentType<{ readonly code: string; readonly language: string }>;

export type SanitizedMarkdownProps = {
  content: string;
  codeBlock?: CodeBlockComponent;
};

const SAFE_LINK_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);
const DATA_IMAGE_URL = /^data:image\/[^;,]+(?:;[^,]*)?,/i;
const URL_SCHEME = /^[a-z][a-z\d+.-]*:/i;

const sanitizeUrl: UrlTransform = (url, key) => {
  const trimmedUrl = url.trim();
  if (trimmedUrl === '' || trimmedUrl.startsWith('#')) {
    return trimmedUrl;
  }

  if (trimmedUrl.startsWith('//')) {
    return undefined;
  }

  if (!URL_SCHEME.test(trimmedUrl)) {
    return trimmedUrl;
  }

  let parsedUrl: URL;
  try {
    parsedUrl = new URL(trimmedUrl);
  } catch {
    return undefined;
  }

  if (key === 'src' && parsedUrl.protocol === 'data:') {
    return DATA_IMAGE_URL.test(trimmedUrl) ? trimmedUrl : undefined;
  }

  return SAFE_LINK_PROTOCOLS.has(parsedUrl.protocol) ? trimmedUrl : undefined;
};

// Tailwind's preflight strips all default browser styling, so every block element react-markdown
// can emit (tables above all) needs an explicit class here or it renders as unstructured text.
const headingComponents: Components = {
  h1: ({ node, ...props }) => {
    void node;
    return <h1 className="mt-3 mb-2 text-xl font-semibold first:mt-0" {...props} />;
  },
  h2: ({ node, ...props }) => {
    void node;
    return <h2 className="mt-3 mb-2 text-lg font-semibold first:mt-0" {...props} />;
  },
  h3: ({ node, ...props }) => {
    void node;
    return <h3 className="mt-3 mb-2 text-base font-semibold first:mt-0" {...props} />;
  },
  h4: ({ node, ...props }) => {
    void node;
    return <h4 className="mt-3 mb-2 text-base font-semibold first:mt-0" {...props} />;
  },
  h5: ({ node, ...props }) => {
    void node;
    return <h5 className="mt-3 mb-2 text-sm font-semibold first:mt-0" {...props} />;
  },
  h6: ({ node, ...props }) => {
    void node;
    return <h6 className="mt-3 mb-2 text-sm font-semibold first:mt-0" {...props} />;
  },
};

const blockComponents: Components = {
  p: ({ node, ...props }) => {
    void node;
    return <p className="mb-2 last:mb-0" {...props} />;
  },
  ul: ({ node, ...props }) => {
    void node;
    return <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0" {...props} />;
  },
  ol: ({ node, ...props }) => {
    void node;
    return <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0" {...props} />;
  },
  li: ({ node, ...props }) => {
    void node;
    return <li className="pl-1" {...props} />;
  },
  // `border-border` blends into the assistant bubble bg in dark mode (both resolve to the same
  // token), so dividers use currentColor, which always contrasts with its own bubble background.
  blockquote: ({ node, ...props }) => {
    void node;
    return <blockquote className="mb-2 border-l-2 border-current/30 pl-3 italic opacity-80 last:mb-0" {...props} />;
  },
  hr: ({ node, ...props }) => {
    void node;
    return <hr className="my-3 border-current/20" {...props} />;
  },
};

// The legacy client drew a full grid (`border: 1px solid` on every cell); row-only rules made the
// same tables read as loose text, so the cell borders are kept.
const tableComponents: Components = {
  table: ({ node, ...props }) => {
    void node;
    return (
      <div className="mb-2 overflow-x-auto last:mb-0">
        <table className="w-full border-collapse text-sm" {...props} />
      </div>
    );
  },
  thead: ({ node, ...props }) => {
    void node;
    return <thead className="bg-current/10" {...props} />;
  },
  tr: ({ node, ...props }) => {
    void node;
    return <tr {...props} />;
  },
  th: ({ node, ...props }) => {
    void node;
    return <th className="border border-current/30 px-3 py-1.5 text-left font-semibold" {...props} />;
  },
  td: ({ node, ...props }) => {
    void node;
    return <td className="border border-current/30 px-3 py-1.5 align-top" {...props} />;
  },
};

const markdownComponents = (CodeBlock: CodeBlockComponent | undefined): Components => ({
  ...headingComponents,
  ...blockComponents,
  ...tableComponents,
  a: ({ node, ...props }) => {
    void node;
    return <a {...props} className="underline underline-offset-2" rel="noopener noreferrer" target="_blank" />;
  },
  img: ({ node, ...props }) => {
    void node;
    return <img {...props} className="max-w-full rounded" />;
  },
  pre: ({ children }) => <>{children}</>,
  code: ({ children, className, node, ...props }) => {
    void node;
    const language = /\blanguage-([^\s]+)/.exec(className ?? '')?.[1];
    if (CodeBlock !== undefined && language !== undefined) {
      return <CodeBlock code={String(children).replace(/\n$/, '')} language={language} />;
    }
    return (
      <code className={cn('rounded bg-black/10 px-1 py-0.5 text-sm dark:bg-white/10', className)} {...props}>
        {children}
      </code>
    );
  },
});

// `remarkBreaks` matches the legacy client's `marked.use({ breaks: true })`: prompt templates
// separate their lines with single newlines, which CommonMark would otherwise collapse.
const REMARK_PLUGINS: MarkdownOptions['remarkPlugins'] = [remarkGfm, remarkBreaks];
const REHYPE_PLUGINS: MarkdownOptions['rehypePlugins'] = [rehypeRaw, [rehypeSanitize, MARKDOWN_SANITIZE_SCHEMA]];

/**
 * Parsing is the dominant cost of showing a transcript (measured at ~70% of the first commit
 * after a conversation switch), and the same text is parsed again every time a conversation is
 * revisited or a bubble remounts. `ReactMarkdown` is a hook-free function that returns a plain
 * element tree, so it is invoked directly and its output kept per content string. Elements are
 * immutable, so sharing one tree between renders is safe.
 */
const MAX_CACHED_RENDERS = 128;

type Renderer = {
  readonly components: Components;
  readonly rendered: Map<string, ReactNode>;
};

const NO_CODE_BLOCK = Symbol('no-code-block');
const renderers = new Map<CodeBlockComponent | typeof NO_CODE_BLOCK, Renderer>();

const rendererFor = (codeBlock: CodeBlockComponent | undefined): Renderer => {
  const key = codeBlock ?? NO_CODE_BLOCK;
  let renderer = renderers.get(key);
  if (renderer === undefined) {
    renderer = { components: markdownComponents(codeBlock), rendered: new Map() };
    renderers.set(key, renderer);
  }
  return renderer;
};

const renderMarkdown = (content: string, codeBlock: CodeBlockComponent | undefined): ReactNode => {
  const { components, rendered } = rendererFor(codeBlock);
  const cached = rendered.get(content);
  if (cached !== undefined) {
    // Re-insert so the map's iteration order doubles as least-recently-used order.
    rendered.delete(content);
    rendered.set(content, cached);
    return cached;
  }

  const tree = ReactMarkdown({
    children: content,
    components,
    rehypePlugins: REHYPE_PLUGINS,
    remarkPlugins: REMARK_PLUGINS,
    urlTransform: sanitizeUrl,
  });

  if (rendered.size >= MAX_CACHED_RENDERS) {
    const oldest = rendered.keys().next().value;
    if (oldest !== undefined) {
      rendered.delete(oldest);
    }
  }
  rendered.set(content, tree);
  return tree;
};

/** Renders remote Markdown through the fixed, shared NFR-SEC-001 sanitisation pipeline. */
export function SanitizedMarkdown({ content, codeBlock }: SanitizedMarkdownProps) {
  return <>{renderMarkdown(content, codeBlock)}</>;
}