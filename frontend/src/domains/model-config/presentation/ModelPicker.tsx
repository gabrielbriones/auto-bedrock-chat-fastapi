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

// FR-CFG-003/003a/003b/016/017: a two-level family → model menu on Base UI's `Menu`
// (`SubmenuRoot`/`SubmenuTrigger`), never a hand-positioned portal — arrow keys, Enter/Space and
// Escape all come from the primitive for free.
export function ModelPicker({ catalog, selectedModelId, disabled = false, onSelect }: ModelPickerProps) {
  const currentFamily = selectedModelId === null ? null : familyOf(catalog, selectedModelId)
  const currentModel = selectedModelId === null ? null : findModel(catalog, selectedModelId)

  return (
    <DropdownMenu>
      <DropdownMenuTrigger disabled={disabled} render={<Button type="button" variant="outline" />}>
        {currentModel?.name ?? MODEL_CONFIG_COPY.modelPicker.trigger}
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        {catalog.families.map((family) => (
          // FR-CFG-003a: the current model's family starts open when the picker is opened.
          <DropdownMenuSub key={family.provider} defaultOpen={family.provider === currentFamily?.provider}>
            <DropdownMenuSubTrigger>{family.provider}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
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
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
