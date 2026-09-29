import type { Page } from '@/domains/knowledge/application/ports'
import type { KnowledgeSnapshot } from '@/domains/knowledge/application/knowledge-snapshot'

// Opening and closing the editor both drop everything scoped to the previously shown document.
export function withDetailReset(
  snapshot: KnowledgeSnapshot,
  detailStatus: KnowledgeSnapshot['detailStatus'],
): KnowledgeSnapshot {
  return {
    ...snapshot,
    activeDocument: null,
    detailStatus,
    detailProblem: null,
    saveProblem: null,
  }
}

// A single-row delete only steps the page back when the removed row was the last one showing.
export function previousOffsetAfterDelete<T>(page: Page<T>): number | null {
  return page.items.length === 1 && page.offset > 0 ? Math.max(0, page.offset - page.limit) : null
}
