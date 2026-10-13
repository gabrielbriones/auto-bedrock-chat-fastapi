import { useState, type FormEvent } from 'react'
import { ChevronDownIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { KNOWLEDGE_COPY } from '@/shared/copy/knowledge'
import { isOk } from '@/shared/kernel/result'

import {
  EMPTY_WEB_CRAWL_DRAFT,
  MAX_MAX_PAGES,
  MIN_MAX_PAGES,
  validateWebCrawlDraft,
  type WebCrawlDraft,
  type WebCrawlIssues,
  type WebCrawlRequest,
} from '@/domains/knowledge/domain/public'
import { DraftCheckbox, DraftTextArea, DraftTextField } from '@/domains/knowledge/presentation/kb-source-fields'

const WEB = KNOWLEDGE_COPY.sources.web

export type WebCrawlFormProps = {
  /** Either form is in flight or a run is active: both forms are held until it finishes. */
  readonly busy: boolean
  readonly submitting: boolean
  readonly onSubmit: (request: WebCrawlRequest) => Promise<boolean>
}

type FieldsProps = {
  readonly draft: WebCrawlDraft
  readonly issues: WebCrawlIssues
  readonly disabled: boolean
  readonly onChange: (patch: Partial<WebCrawlDraft>) => void
}

function IdentityFields({ draft, issues, disabled, onChange }: FieldsProps) {
  return (
    <>
      <DraftTextField label={WEB.name} value={draft.name} issue={issues.name} disabled={disabled} onChange={(name) => onChange({ name })} />
      <DraftTextArea label={WEB.urls} value={draft.urls} issue={issues.urls} disabled={disabled} onChange={(urls) => onChange({ urls })} />
      <DraftTextField label={WEB.topic} value={draft.topic} disabled={disabled} onChange={(topic) => onChange({ topic })} />
    </>
  )
}

function LimitFields({ draft, issues, disabled, onChange }: FieldsProps) {
  return (
    <>
      <div className="grid gap-5 sm:grid-cols-2">
        <DraftTextField label={WEB.maxDepth} type="number" min={0} value={draft.maxDepth} issue={issues.maxDepth} disabled={disabled} onChange={(maxDepth) => onChange({ maxDepth })} />
        <DraftTextField label={WEB.maxPages} type="number" min={MIN_MAX_PAGES} max={MAX_MAX_PAGES} value={draft.maxPages} issue={issues.maxPages} disabled={disabled} onChange={(maxPages) => onChange({ maxPages })} />
      </div>
      <DraftTextField label={WEB.allowedDomains} hint={WEB.allowedDomainsHint} value={draft.allowedDomains} disabled={disabled} onChange={(allowedDomains) => onChange({ allowedDomains })} />
      <DraftTextField label={WEB.excludePatterns} value={draft.excludePatterns} disabled={disabled} onChange={(excludePatterns) => onChange({ excludePatterns })} />
    </>
  )
}

// Linked PDFs (XMGPLAT-11400) and AI synthesis (XMGPLAT-11801) are both off unless opted into.
function OptInFields({ draft, disabled, onChange }: FieldsProps) {
  return (
    <>
      <DraftCheckbox label={WEB.ingestLinkedFiles} hint={WEB.ingestLinkedFilesHint} checked={draft.ingestLinkedFiles} disabled={disabled} onChange={(ingestLinkedFiles) => onChange({ ingestLinkedFiles })} />
      <DraftCheckbox label={WEB.synthesize} hint={WEB.synthesizeHint} checked={draft.synthesize} disabled={disabled} onChange={(synthesize) => onChange({ synthesize })} />
    </>
  )
}

// Collapsed by default: these carry bearer tokens and session cookies.
function AdvancedFields({ draft, issues, disabled, onChange }: FieldsProps) {
  const [open, setOpen] = useState(issues.headers !== undefined || issues.cookies !== undefined)

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="grid gap-4">
      <CollapsibleTrigger
        render={<Button type="button" variant="outline" className="w-fit" />}
      >
        <ChevronDownIcon aria-hidden className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
        {WEB.advanced}
      </CollapsibleTrigger>
      <CollapsibleContent className="grid gap-4">
        <DraftTextArea label={WEB.headers} mono placeholder={WEB.headersPlaceholder} value={draft.headers} issue={issues.headers} disabled={disabled} onChange={(headers) => onChange({ headers })} />
        <DraftTextArea label={WEB.cookies} mono placeholder={WEB.cookiesPlaceholder} value={draft.cookies} issue={issues.cookies} disabled={disabled} onChange={(cookies) => onChange({ cookies })} />
        <p className="text-sm text-muted-foreground">{WEB.advancedHint}</p>
      </CollapsibleContent>
    </Collapsible>
  )
}

export function WebCrawlForm({ busy, submitting, onSubmit }: WebCrawlFormProps) {
  const [draft, setDraft] = useState<WebCrawlDraft>(EMPTY_WEB_CRAWL_DRAFT)
  const [issues, setIssues] = useState<WebCrawlIssues>({})
  const patch = (change: Partial<WebCrawlDraft>) => setDraft((current) => ({ ...current, ...change }))

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const validated = validateWebCrawlDraft(draft)
    if (!isOk(validated)) {
      setIssues(validated.error)
      return
    }
    setIssues({})
    void onSubmit(validated.value)
  }

  const fields = { draft, issues, disabled: busy, onChange: patch }

  return (
    <form className="grid gap-5" aria-label={WEB.title} onSubmit={submit}>
      <IdentityFields {...fields} />
      <LimitFields {...fields} />
      <OptInFields {...fields} />
      <AdvancedFields {...fields} />
      <Button type="submit" className="w-fit" disabled={busy} aria-label={submitting ? WEB.starting : undefined}>
        {WEB.submit}
      </Button>
    </form>
  )
}
