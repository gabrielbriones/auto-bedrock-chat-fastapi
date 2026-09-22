import { useState } from 'react'
import { Loader2Icon, Settings2Icon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { MODEL_CONFIG_COPY } from '@/shared/copy/model-config'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { effectiveModelId, resolveEffectiveModel } from '@/domains/model-config/domain/effective-model'
import { overrideCount } from '@/domains/model-config/domain/override-delta'
import { SettingsSheet } from '@/domains/model-config/presentation/SettingsSheet'

export type ModelConfigHeaderProps = {
  readonly configuredModel: { readonly id: string; readonly name: string } | null
}

export function ModelConfigHeader({ configuredModel }: ModelConfigHeaderProps) {
  const { bootstrap, modelConfig } = useContainer()
  const { profile, pendingKeys, resetPending, rejectionReasons } = useContainerStore('modelConfig')
  const [open, setOpen] = useState(false)
  const count = overrideCount(profile)
  const modelId = effectiveModelId(profile)
  const modelName = configuredModel?.id === modelId
    ? configuredModel.name
    : (resolveEffectiveModel(profile)?.name ?? modelId ?? bootstrap.modelDisplayName)
  const pending = pendingKeys.size > 0 || resetPending

  return (
    <>
      <Button type="button" variant="ghost" className="max-w-64" aria-label={MODEL_CONFIG_COPY.sheet.trigger} onClick={() => setOpen(true)}>
        {pending ? <Loader2Icon aria-hidden className="animate-spin" /> : <Settings2Icon aria-hidden />}
        <span className="truncate">{modelName}</span>
        {count === 0 ? null : <Badge>{count}</Badge>}
      </Button>
      <SettingsSheet
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