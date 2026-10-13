import { CheckIcon, CopyIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { MESSAGING_COPY } from '@/shared/copy/messaging'

type ShikiToken = {
  readonly content: string
  readonly color?: string
  readonly fontStyle?: number
}

export type CodeBlockProps = {
  readonly code: string
  readonly language: string
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
  ]).then(([core, engine, theme]) =>
    core.createHighlighterCore({
      engine: engine.createJavaScriptRegexEngine(),
      themes: [theme.default],
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
  return instance.codeToTokens(code, { lang: language as never, theme: 'github-light' as never }).tokens
}

function TokenLines({ tokens }: { readonly tokens: readonly (readonly ShikiToken[])[] }) {
  return tokens.map((line, lineIndex) => (
    <span key={lineIndex}>
      {line.map((token, tokenIndex) => (
        <span
          key={tokenIndex}
          style={{
            color: token.color,
            fontStyle: token.fontStyle === 1 ? 'italic' : undefined,
            fontWeight: token.fontStyle === 2 ? 'bold' : undefined,
          }}
        >
          {token.content}
        </span>
      ))}
      {'\n'}
    </span>
  ))
}

/** Loads Shiki only for fenced Markdown and renders its tokens as React nodes, never raw HTML. */
export function CodeBlock({ code, language }: CodeBlockProps) {
  const [tokens, setTokens] = useState<readonly (readonly ShikiToken[])[] | null>(null)
  const [copied, setCopied] = useState(false)

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

  const copy = () => {
    void navigator.clipboard?.writeText(code).then(() => setCopied(true))
  }

  const renderedTokens: readonly (readonly ShikiToken[])[] =
    tokens ?? [code.split('\n').map((content): ShikiToken => ({ content }))]

  return (
    <div className="relative my-3 overflow-hidden rounded-lg border border-border bg-muted/50">
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
      <pre className="overflow-x-auto p-4 pr-10 text-sm" data-language={language}>
        <code>
          <TokenLines tokens={renderedTokens} />
        </code>
      </pre>
    </div>
  )
}