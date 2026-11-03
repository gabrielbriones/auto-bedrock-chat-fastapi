import { stripReasoningBlocks } from '@/domains/messaging/presentation/redaction'

const MAX_PREVIEW_LENGTH = 160

// FR-MSG-032. An OS notification body is plain text, so the reply's Markdown is flattened to
// something readable at a glance. Identifiers like `extract_job_metrics` keep their underscores.
// Reasoning is hidden exactly as the transcript hides it, before any other tag is flattened.
export const replyPreview = (text: string, maxLength = MAX_PREVIEW_LENGTH): string => {
  const plain = stripReasoningBlocks(text)
    .replace(/```[^\n]*/g, ' ')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]*>/g, ' ')
    .replace(/^[ \t]{0,3}(?:#{1,6}|>|[-*+]|\d+\.)[ \t]+/gm, '')
    .replace(/\*\*|__|~~|`|\*/g, '')
    .replace(/\s+/g, ' ')
    .trim()

  return plain.length <= maxLength ? plain : `${plain.slice(0, maxLength - 1).trimEnd()}…`
}
