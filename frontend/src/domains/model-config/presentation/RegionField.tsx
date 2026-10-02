import { useId } from 'react'
import { Loader2Icon } from 'lucide-react'

import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { MODEL_CONFIG_COPY } from '@/shared/copy/model-config'

import { regionOf, regionVariants, type ModelCatalog } from '@/domains/model-config/domain/model-catalog'

export type RegionFieldProps = {
  readonly catalog: ModelCatalog
  readonly modelId: string
  readonly pending: boolean
  readonly onSelect: (modelId: string) => void
}

const REGION_NAMES: Readonly<Record<string, string>> = MODEL_CONFIG_COPY.region.names

const regionLabel = (modelId: string): string => {
  const region = regionOf(modelId)
  return region === null ? MODEL_CONFIG_COPY.region.default : (REGION_NAMES[region] ?? region.toUpperCase())
}

// Picks among the cross-region inference profiles of the current model; hidden when it has none.
export function RegionField({ catalog, modelId, pending, onSelect }: RegionFieldProps) {
  const id = useId()
  const variants = regionVariants(catalog, modelId)

  if (variants.length < 2) {
    return null
  }

  return (
    <>
      <div className="grid gap-2">
        <div className="flex items-center gap-1.5">
          <Label htmlFor={id}>{MODEL_CONFIG_COPY.region.label}</Label>
          {pending ? (
            <span role="status" className="inline-flex text-muted-foreground">
              <Loader2Icon aria-hidden className="size-3.5 animate-spin" />
              <span className="sr-only">{MODEL_CONFIG_COPY.pending}</span>
            </span>
          ) : null}
        </div>
        <Select
          value={modelId}
          disabled={pending}
          onValueChange={(value: string | null) => {
            if (value !== null && value !== modelId) onSelect(value)
          }}
        >
          <SelectTrigger id={id} className="w-full">
            <SelectValue>{regionLabel(modelId)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {variants.map((variant) => (
              <SelectItem key={variant.id} value={variant.id}>
                {regionLabel(variant.id)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {/* Full-bleed, like the header border: cancels the scroll area's px-6. */}
      <Separator className="-mx-6 data-horizontal:w-auto" />
    </>
  )
}
