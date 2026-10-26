import { CheckIcon, CopyIcon } from 'lucide-react'
import { useEffect, useState, type CSSProperties } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

type ShikiTokenStyle = {
  readonly color?: string
  readonly fontStyle?: number
}

type ShikiToken = {
  readonly content: string
  readonly variants?: Readonly<Record<string, ShikiTokenStyle>>
}

const THEMES = ['light', 'dark'] as const

export type CodeBlockProps = {
  readonly code: string
  readonly language: string
  /** Soft-wraps long lines instead of scrolling sideways; suited to data payloads rather than source. */
  readonly wrap?: boolean
  readonly className?: string
}

const LANGUAGE_LOADERS: Record<string, () => Promise<unknown>> = {
  bash: () => import('shiki/langs/bash.mjs'),
  javascript: () => import('shiki/langs/javascript.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  sh: () => import('shiki/langs/shellscript.mjs'),
  shell: () => import('shiki/langs/shellscript.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  ts: () => import('shiki/langs/typescript.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'),
}

let highlighter: Promise<Awaited<ReturnType<typeof import('shiki/core')['createHighlighterCore']>>> | null = null

const getHighlighter = () => {
  highlighter ??= Promise.all([
    import('shiki/core'),
    import('shiki/engine/javascript'),
    import('shiki/themes/github-light.mjs'),
    import('shiki/themes/github-dark.mjs'),
  ]).then(([core, engine, light, dark]) =>
    core.createHighlighterCore({
      engine: engine.createJavaScriptRegexEngine(),
      themes: [light.default, dark.default],
      langs: [],
    }),
  )
  return highlighter
}

const highlight = async (code: string, language: string): Promise<readonly (readonly ShikiToken[])[] | null> => {
  const loadLanguage = LANGUAGE_LOADERS[language.toLowerCase()]
  if (loadLanguage === undefined) {
    return null
  }

  const [instance, languageModule] = await Promise.all([getHighlighter(), loadLanguage()])
  await instance.loadLanguage(languageModule as never)
  return instance.codeToTokensWithThemes(code, {
    lang: language as never,
    themes: { light: 'github-light', dark: 'github-dark' } as never,
  })
}

/** Exposes each theme's colours as CSS variables so the `code-token` utility can follow the colour scheme. */
const tokenStyle = (variants: ShikiToken['variants']): CSSProperties | undefined => {
  if (variants === undefined) {
    return undefined
  }
  const style: Record<string, string> = {}
  for (const theme of THEMES) {
    const variant = variants[theme]
    if (variant?.color !== undefined) {
      style[`--shiki-${theme}`] = variant.color
    }
    if (variant?.fontStyle === 1) {
      style[`--shiki-${theme}-font-style`] = 'italic'
    } else if (variant?.fontStyle === 2) {
      style[`--shiki-${theme}-font-weight`] = 'bold'
    }
  }
  return style as CSSProperties
}

function TokenLines({ tokens }: { readonly tokens: readonly (readonly ShikiToken[])[] }) {
  return tokens.map((line, lineIndex) => (
    <span key={lineIndex}>
      {line.map((token, tokenIndex) => (
        <span key={tokenIndex} className="code-token" style={tokenStyle(token.variants)}>
          {token.content}
        </span>
      ))}
      {'\n'}
    </span>
  ))
}

function useHighlightedTokens(code: string, language: string): readonly (readonly ShikiToken[])[] {
  const [tokens, setTokens] = useState<readonly (readonly ShikiToken[])[] | null>(null)

  useEffect(() => {
    let cancelled = false

    void highlight(code, language)
      .then((result) => {
        if (!cancelled) {
          setTokens(result)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTokens(null)
        }
      })

    return () => {
      cancelled = true
    }
  }, [code, language])

  return tokens ?? [code.split('\n').map((content): ShikiToken => ({ content }))]
}

/** Loads Shiki only for fenced Markdown and renders its tokens as React nodes, never raw HTML. */
export function CodeBlock({ code, language, wrap = false, className }: CodeBlockProps) {
  const renderedTokens = useHighlightedTokens(code, language)
  const [copied, setCopied] = useState(false)

  const copy = () => {
    void navigator.clipboard?.writeText(code).then(() => setCopied(true))
  }

  return (
    <div
      className={cn(
        'relative my-3 overflow-hidden rounded-lg border border-border bg-muted/50 dark:border-white/15 dark:bg-code-bg',
        className,
      )}
    >
      <Button
        aria-label={MESSAGING_COPY.transcript.copyCode}
        className="absolute top-2 right-2"
        onClick={copy}
        size="icon-xs"
        title={MESSAGING_COPY.transcript.copyCode}
        type="button"
        variant="ghost"
      >
        {copied ? <CheckIcon aria-hidden="true" /> : <CopyIcon aria-hidden="true" />}
      </Button>
      <pre
        className={cn('p-4 pr-10 text-sm', wrap ? 'whitespace-pre-wrap break-words' : 'overflow-x-auto')}
        data-language={language}
      >
        <code>
          <TokenLines tokens={renderedTokens} />
        </code>
      </pre>
    </div>
  )
}