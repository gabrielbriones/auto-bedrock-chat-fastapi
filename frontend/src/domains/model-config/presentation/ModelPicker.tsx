import { useState } from 'react'

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

import { familyOf, findModel, type ModelCatalog } from '@/domains/model-config/domain/model-catalog'

export type ModelPickerProps = {
  readonly catalog: ModelCatalog
  readonly selectedModelId: string | null
  readonly disabled?: boolean
  readonly onSelect: (modelId: string) => void
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
      {family.models.map((model) => (
        // FR-CFG-003a: the built-in radio indicator marks the current model as selected.
        <DropdownMenuRadioItem key={model.id} value={model.id} className="justify-between gap-4">
          {model.name}
          {model.supportsTemperature ? null : (
            <span className="text-xs text-muted-foreground">{MODEL_CONFIG_COPY.modelPicker.noTemperatureSupport}</span>
          )}
        </DropdownMenuRadioItem>
      ))}
    </DropdownMenuRadioGroup>
  )
}

// FR-CFG-003/003a/003b/016/017: a two-level family → model menu on Base UI's `Menu`
// (`SubmenuRoot`/`SubmenuTrigger`), never a hand-positioned portal — arrow keys, Enter/Space and
// Escape all come from the primitive for free.
export function ModelPicker({ catalog, selectedModelId, disabled = false, onSelect }: ModelPickerProps) {
  const currentFamily = selectedModelId === null ? null : familyOf(catalog, selectedModelId)
  const currentModel = selectedModelId === null ? null : findModel(catalog, selectedModelId)
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
      <DropdownMenuTrigger disabled={disabled} render={<Button type="button" variant="outline" />}>
        {currentModel?.name ?? MODEL_CONFIG_COPY.modelPicker.trigger}
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {catalog.families.map((family) => (
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
              className="max-h-[min(70vh,36rem)] max-w-[calc(100vw-1rem)] overflow-y-auto"
            >
              <FamilyModels family={family} selectedModelId={selectedModelId} onSelect={onSelect} />
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
