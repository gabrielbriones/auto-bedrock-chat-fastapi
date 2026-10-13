import { RotateCcwIcon, XIcon } from 'lucide-react'

import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { MODEL_CONFIG_COPY } from '@/shared/copy/model-config'

import { clampMaxTokens, maxTokensCap } from '@/domains/model-config/domain/clamp'
import { effective, type ConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { effectiveModelId, resolveEffectiveModel } from '@/domains/model-config/domain/effective-model'
import { overriddenKeys } from '@/domains/model-config/domain/override-delta'
import { overrideFieldFor } from '@/domains/model-config/domain/override-field'
import type { OverrideKey, OverrideValue } from '@/domains/model-config/domain/override-key'
import { visibleFields } from '@/domains/model-config/domain/visible-fields'
import { OverrideField } from '@/domains/model-config/presentation/OverrideField'
import { RegionField } from '@/domains/model-config/presentation/RegionField'

export type SettingsDialogProps = {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly profile: ConfigurationProfile
  readonly pendingKeys?: ReadonlySet<OverrideKey>
  readonly resetPending?: boolean
  readonly rejectionReasons?: readonly string[]
  readonly onCommit: (key: OverrideKey, value: OverrideValue) => void
  readonly onDismissRejections?: () => void
  readonly onReset?: () => void
}

function RejectionAlert({ reasons, onDismiss }: { readonly reasons: readonly string[]; readonly onDismiss?: () => void }) {
  if (reasons.length === 0) {
    return null
  }

  return (
    <Alert variant="destructive">
      <AlertTitle>{MODEL_CONFIG_COPY.rejection.title}</AlertTitle>
      <AlertDescription>{reasons.join('; ')}</AlertDescription>
      {onDismiss === undefined ? null : (
        <AlertAction>
          <Button type="button" variant="ghost" size="icon-xs" aria-label={MODEL_CONFIG_COPY.rejection.dismiss} onClick={onDismiss}>
            <XIcon aria-hidden />
          </Button>
        </AlertAction>
      )}
    </Alert>
  )
}

function ResetButton({ disabled, pending, onReset }: { readonly disabled: boolean; readonly pending: boolean; readonly onReset: () => void }) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="text-muted-foreground hover:text-foreground"
      disabled={disabled || pending}
      onClick={onReset}
    >
      <RotateCcwIcon aria-hidden />
      {pending ? MODEL_CONFIG_COPY.pending : MODEL_CONFIG_COPY.reset}
    </Button>
  )
}

const commitWithCap = (
  onCommit: SettingsDialogProps['onCommit'],
  effectiveModel: ReturnType<typeof resolveEffectiveModel>,
) => (key: OverrideKey, value: OverrideValue) => {
  onCommit(key, key === 'max_tokens' && typeof value === 'number' ? clampMaxTokens(value, effectiveModel) : value)
}

// SPEC-014 §5/FR-CFG-010: a dismissible dialog (Base UI `Dialog`, so it already brings the focus
// trap, `Escape` close and focus restoration). The model itself is picked from the composer, so
// only the remaining `visibleFields(profile)` render here (FR-CFG-002/004).
export function SettingsDialog({
  open,
  onOpenChange,
  profile,
  pendingKeys = new Set(),
  resetPending = false,
  rejectionReasons = [],
  onCommit,
  onDismissRejections,
  onReset,
}: SettingsDialogProps) {
  const overridden = overriddenKeys(profile)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="flex max-h-[85vh] flex-col gap-0 p-0 sm:max-w-lg">
        <DialogHeader className="flex-row items-center gap-1 border-b border-border py-3 ps-6 pe-3">
          <DialogTitle className="me-auto">{MODEL_CONFIG_COPY.dialog.title}</DialogTitle>
          {onReset === undefined ? null : (
            <ResetButton disabled={overridden.length === 0} pending={resetPending} onReset={onReset} />
          )}
          <DialogClose render={<Button type="button" variant="ghost" size="icon-sm" aria-label={MODEL_CONFIG_COPY.dialog.close} />}>
            <XIcon aria-hidden />
          </DialogClose>
        </DialogHeader>
        {/* Slider thumbs at min/max poke past the edge; clip them instead of scrolling sideways. */}
        <div className="grid min-h-0 gap-6 overflow-x-hidden overflow-y-auto px-6 py-5">
          <RejectionAlert
            reasons={rejectionReasons}
            {...(onDismissRejections === undefined ? {} : { onDismiss: onDismissRejections })}
          />
          <SettingsFields profile={profile} pendingKeys={pendingKeys} onCommit={onCommit} />
        </div>
      </DialogContent>
    </Dialog>
  )
}

type SettingsFieldsProps = Pick<SettingsDialogProps, 'profile' | 'onCommit'> & {
  readonly pendingKeys: ReadonlySet<OverrideKey>
}

function SettingsFields({ profile, pendingKeys, onCommit }: SettingsFieldsProps) {
  const overridden = overriddenKeys(profile)
  const effectiveModel = resolveEffectiveModel(profile)
  const modelId = effectiveModelId(profile)
  const fields = visibleFields(profile)

  // FR-CFG-005a: a typed max_tokens above the live cap is clamped before it is ever sent.
  const handleCommit = commitWithCap(onCommit, effectiveModel)

  return (
    <>
      {modelId !== null && fields.includes('model_id') ? (
        <RegionField
          catalog={profile.catalog}
          modelId={modelId}
          pending={pendingKeys.has('model_id')}
          onSelect={(id) => onCommit('model_id', id)}
        />
      ) : null}
      {fields.filter((key) => key !== 'model_id').map((key) => (
        <OverrideField
          key={key}
          field={key === 'max_tokens'
            ? { ...overrideFieldFor(key), max: maxTokensCap(effectiveModel) }
            : overrideFieldFor(key)}
          value={effective(profile, key)}
          overridden={overridden.includes(key)}
          pending={pendingKeys.has(key)}
          catalog={profile.catalog}
          onCommit={handleCommit}
        />
      ))}
    </>
  )
}
