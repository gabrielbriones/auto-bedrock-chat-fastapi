import { Badge } from '@/components/ui/badge'
import { PROMPT_CATALOG_COPY } from '@/shared/copy/prompt-catalog'

// FR-PROMPT-015: shown until the user edits the field — the store clears `detected` on edit.
export function DetectedValueBadge() {
  return <Badge variant="outline">{PROMPT_CATALOG_COPY.detectedBadge}</Badge>
}
