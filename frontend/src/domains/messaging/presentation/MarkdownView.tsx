import { memo } from 'react'

import { CodeBlock } from '@/domains/messaging/presentation/CodeBlock'
import { SanitizedMarkdown } from '@/shared/markdown/sanitized-markdown'

export type MarkdownViewProps = {
  readonly content: string
}

/** The only assistant Markdown entry point: sanitised before custom code rendering. */
export const MarkdownView = memo(function MarkdownView({ content }: MarkdownViewProps) {
  return <SanitizedMarkdown codeBlock={CodeBlock} content={content} />
})