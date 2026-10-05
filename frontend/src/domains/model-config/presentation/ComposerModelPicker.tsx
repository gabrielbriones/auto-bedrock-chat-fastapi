import { useState } from 'react'
import { Loader2Icon, SlidersHorizontalIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { DropdownMenuItem, DropdownMenuSeparator } from '@/components/ui/dropdown-menu'
import { MODEL_CONFIG_COPY } from '@/shared/copy/model-config'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { effectiveModelId, resolveEffectiveModel } from '@/domains/model-config/domain/effective-model'
import { regionVariants } from '@/domains/model-config/domain/model-catalog'
import { visibleFields } from '@/domains/model-config/domain/visible-fields'
import { ModelPicker } from '@/domains/model-config/presentation/ModelPicker'
import { ProviderLogo } from '@/domains/model-config/presentation/ProviderLogo'
import { SettingsDialog } from '@/domains/model-config/presentation/SettingsDialog'

export type ComposerModelPickerProps = {
  readonly configuredModel: { readonly id: string; readonly name: string } | null
}

const TRIGGER = (
  <Button
    type="button"
    variant="ghost"
    size="sm"
    className="max-w-64 text-sm text-muted-foreground hover:bg-transparent hover:text-foreground aria-expanded:bg-transparent aria-expanded:text-foreground dark:hover:bg-transparent"
  />
)

function TriggerContent({ name, provider, pending }: { readonly name: string; readonly provider: string | null; readonly pending: boolean }) {
  return (
    <>
      {pending ? (
        <>
          <Loader2Icon aria-hidden className="animate-spin" />
          <span className="sr-only">{MODEL_CONFIG_COPY.pending}</span>
        </>
      ) : <ProviderLogo provider={provider} />}
      <span className="truncate">{name}</span>
    </>
  )
}

// Lives in the composer (as in Copilot/ChatGPT/Gemini): the model is the setting that changes
// often; the rest (temperature, max tokens, ...) sits one step away in `SettingsDialog`.
export function ComposerModelPicker({ configuredModel }: ComposerModelPickerProps) {
  const { bootstrap, modelConfig } = useContainer()
  const { profile, pendingKeys, resetPending, rejectionReasons } = useContainerStore('modelConfig')
  const [open, setOpen] = useState(false)
  const modelId = effectiveModelId(profile)
  const effectiveModel = resolveEffectiveModel(profile)
  const baseModel = modelId === null ? undefined : regionVariants(profile.catalog, modelId)[0]
  const modelName = baseModel !== undefined && baseModel.id !== modelId
    ? baseModel.name
    : configuredModel?.id === modelId
      ? configuredModel.name
      : (effectiveModel?.name ?? modelId ?? bootstrap.modelDisplayName)
  const pending = pendingKeys.size > 0 || resetPending
  const selectable = visibleFields(profile).includes('model_id')

  return (
    <>
      <ModelPicker
        catalog={profile.catalog}
        selectedModelId={modelId}
        disabled={pendingKeys.has('model_id')}
        selectable={selectable}
        onSelect={(id) => modelConfig.commit('model_id', id)}
        triggerRender={TRIGGER}
        triggerContent={<TriggerContent name={modelName} provider={effectiveModel?.provider ?? null} pending={pending} />}
        footer={
          <>
            {selectable ? <DropdownMenuSeparator /> : null}
            <DropdownMenuItem onClick={() => setOpen(true)}>
              <SlidersHorizontalIcon aria-hidden />
              {MODEL_CONFIG_COPY.dialog.open}
            </DropdownMenuItem>
          </>
        }
      />
      <SettingsDialog
        open={open}
        onOpenChange={setOpen}
        profile={profile}
        pendingKeys={pendingKeys}
        resetPending={resetPending}
        rejectionReasons={rejectionReasons}
        onCommit={(key, value) => modelConfig.commit(key, value)}
        onDismissRejections={() => modelConfig.dismissRejections()}
        onReset={() => modelConfig.reset()}
      />
    </>
  )
}