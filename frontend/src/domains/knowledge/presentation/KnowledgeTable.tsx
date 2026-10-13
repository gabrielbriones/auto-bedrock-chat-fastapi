import type { ReactNode } from 'react'
import {
  FlagIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  TriangleAlertIcon,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { DataTable, type DataTableColumn } from '@/components/ui/composed/data-table'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'

import type { CredibilityBand, KbDocumentSummary } from '@/domains/knowledge/domain/public'

const credibilityLabel: Record<CredibilityBand, string> = {
  danger: KNOWLEDGE_COPY.credibility.low,
  warn: KNOWLEDGE_COPY.credibility.review,
  good: KNOWLEDGE_COPY.credibility.high,
}

function CredibilityBadge({ band, score }: { readonly band: CredibilityBand; readonly score: number }) {
  const Icon = band === 'good' ? ShieldCheckIcon : band === 'warn' ? TriangleAlertIcon : ShieldAlertIcon

  return (
    <Badge variant="outline" className="gap-1 whitespace-nowrap">
      <Icon aria-hidden size={14} />
      {credibilityLabel[band]} ({Math.round(score * 100)}%)
    </Badge>
  )
}

const visibleTags = (tags: readonly string[]): ReactNode => (
  <div className="flex max-w-64 flex-wrap gap-1">
    {tags.slice(0, 5).map((tag) => (
      <Badge key={tag} variant="outline">{tag}</Badge>
    ))}
    {tags.length > 5 ? <Badge variant="secondary">{KNOWLEDGE_COPY.table.moreTags(tags.length - 5)}</Badge> : null}
  </div>
)

const baseColumns: readonly DataTableColumn<KbDocumentSummary>[] = [
  {
    id: 'title',
    header: KNOWLEDGE_COPY.table.title,
    cell: (document) => document.title ?? KNOWLEDGE_COPY.table.untitled,
    className: 'max-w-[24rem] whitespace-normal align-top font-medium',
  },
  { id: 'source', header: KNOWLEDGE_COPY.table.source, cell: (document) => document.source ?? KNOWLEDGE_COPY.table.notAvailable, className: 'align-top' },
  { id: 'topic', header: KNOWLEDGE_COPY.table.topic, cell: (document) => document.topic ?? KNOWLEDGE_COPY.table.notAvailable, className: 'align-top' },
  { id: 'tags', header: KNOWLEDGE_COPY.table.tags, cell: (document) => visibleTags(document.tags), className: 'whitespace-normal align-top' },
  {
    id: 'credibility',
    header: KNOWLEDGE_COPY.table.credibility,
    cell: (document) => <CredibilityBadge band={document.credibility.band} score={document.credibility.score} />,
    className: 'align-top',
  },
  {
    id: 'flagged',
    header: KNOWLEDGE_COPY.table.status,
    cell: (document) => document.credibility.removalFlagged ? (
      <span className="inline-flex items-center gap-1 font-medium">
        <FlagIcon aria-hidden size={14} />
        {KNOWLEDGE_COPY.table.flagged}
      </span>
    ) : (
      <span>{KNOWLEDGE_COPY.table.notFlagged}</span>
    ),
    className: 'align-top',
  },
]

export type KnowledgeTableProps = {
  readonly rows: readonly KbDocumentSummary[]
  readonly loading: boolean
  readonly error?: ReactNode
  readonly onOpen: (document: KbDocumentSummary) => void
}

export function KnowledgeTable({ rows, loading, error, onOpen }: KnowledgeTableProps) {
  return (
    <DataTable
      caption={KNOWLEDGE_COPY.table.caption}
      columns={baseColumns}
      rows={rows}
      rowKey={(document) => document.id}
      rowAction={{ label: (document) => document.title ?? KNOWLEDGE_COPY.table.untitled, onActivate: onOpen }}
      isLoading={loading}
      {...(error === undefined ? {} : { error })}
      emptyTitle={KNOWLEDGE_COPY.browser.empty}
    />
  )
}