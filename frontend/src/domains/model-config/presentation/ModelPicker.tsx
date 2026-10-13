import { useState, type ReactElement, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { MODEL_CONFIG_COPY } from '@/shared/copy/model-config'

import { baseModels, familyOf, findModel, regionVariants, type ModelCatalog } from '@/domains/model-config/domain/model-catalog'

export type ModelPickerProps = {
  readonly catalog: ModelCatalog
  readonly selectedModelId: string | null
  readonly disabled?: boolean
  readonly onSelect: (modelId: string) => void
  /** False when the allow-list forbids `model_id`: the families are hidden, the footer stays. */
  readonly selectable?: boolean
  readonly triggerRender?: ReactElement
  readonly triggerContent?: ReactNode
  readonly footer?: ReactNode
}

type FamilyModelsProps = Pick<ModelPickerProps, 'selectedModelId' | 'onSelect'> & {
  readonly family: ModelCatalog['families'][number]
}

function FamilyModels({ family, selectedModelId, onSelect }: FamilyModelsProps) {
  return (
    <DropdownMenuRadioGroup
      value={selectedModelId}
      onValueChange={(value: unknown) => {
        if (typeof value === 'string') {
          onSelect(value)
        }
      }}
    >
      {baseModels(family).map((model) => (
        // FR-CFG-003a: the built-in radio indicator marks the current model as selected. Regional
        // variants collapse into one entry; the region is picked in the settings dialog.
        <DropdownMenuRadioItem key={model.id} value={model.id}>
          {model.name}
        </DropdownMenuRadioItem>
      ))}
    </DropdownMenuRadioGroup>
  )
}

const DEFAULT_TRIGGER = <Button type="button" variant="outline" />

// FR-CFG-003/003a/003b/016/017: a two-level family → model menu on Base UI's `Menu`
// (`SubmenuRoot`/`SubmenuTrigger`), never a hand-positioned portal — arrow keys, Enter/Space and
// Escape all come from the primitive for free.
export function ModelPicker({
  catalog, selectedModelId, disabled = false, onSelect, selectable = true, triggerRender = DEFAULT_TRIGGER, triggerContent, footer,
}: ModelPickerProps) {
  const currentFamily = selectedModelId === null ? null : familyOf(catalog, selectedModelId)
  const currentModel = selectedModelId === null ? null : findModel(catalog, selectedModelId)
  const selectedBaseId = selectedModelId === null ? null : (regionVariants(catalog, selectedModelId)[0]?.id ?? selectedModelId)
  // FR-CFG-003a, deferred to onOpenChangeComplete: opened alongside the parent, the submenu anchors to a still-moving trigger and jumps.
  const [openFamily, setOpenFamily] = useState<string | null>(null)

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        if (!open) {
          setOpenFamily(null)
        }
      }}
      onOpenChangeComplete={(open) => {
        if (open) {
          setOpenFamily((prev) => prev ?? currentFamily?.provider ?? null)
        }
      }}
    >
      <DropdownMenuTrigger disabled={disabled} render={triggerRender}>
        {triggerContent ?? currentModel?.name ?? MODEL_CONFIG_COPY.modelPicker.trigger}
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-auto min-w-48">
        {(selectable ? catalog.families : []).map((family) => (
          <DropdownMenuSub
            key={family.provider}
            open={openFamily === family.provider}
            onOpenChange={(open) => {
              setOpenFamily((prev) => (open ? family.provider : prev === family.provider ? null : prev))
            }}
          >
            <DropdownMenuSubTrigger>{family.provider}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent
              sideOffset={10}
              // Grow upward from the family row: the picker lives at the bottom of the viewport.
              align="end"
              className="max-h-[min(70vh,36rem)] max-w-[calc(100vw-1rem)] overflow-y-auto"
            >
              <FamilyModels family={family} selectedModelId={selectedBaseId} onSelect={onSelect} />
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        ))}
        {footer}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
