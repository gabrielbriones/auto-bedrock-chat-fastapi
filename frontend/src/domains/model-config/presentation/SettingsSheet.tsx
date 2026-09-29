import { RotateCcwIcon, XIcon } from 'lucide-react'

import { Alert, AlertAction, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { MODEL_CONFIG_COPY } from '@/shared/copy/model-config'

import { clampMaxTokens, maxTokensCap } from '@/domains/model-config/domain/clamp'
import { effective, type ConfigurationProfile } from '@/domains/model-config/domain/configuration-profile'
import { resolveEffectiveModel } from '@/domains/model-config/domain/effective-model'
import { overriddenKeys } from '@/domains/model-config/domain/override-delta'
import { overrideFieldFor } from '@/domains/model-config/domain/override-field'
import type { OverrideKey, OverrideValue } from '@/domains/model-config/domain/override-key'
import { visibleFields } from '@/domains/model-config/domain/visible-fields'
import { OverrideField } from '@/domains/model-config/presentation/OverrideField'

export type SettingsSheetProps = {
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
    <Button type="button" variant="outline" disabled={disabled || pending} onClick={onReset}>
      <RotateCcwIcon aria-hidden />
      {pending ? MODEL_CONFIG_COPY.pending : MODEL_CONFIG_COPY.reset}
    </Button>
  )
}

const commitWithCap = (
  onCommit: SettingsSheetProps['onCommit'],
  effectiveModel: ReturnType<typeof resolveEffectiveModel>,
) => (key: OverrideKey, value: OverrideValue) => {
  onCommit(key, key === 'max_tokens' && typeof value === 'number' ? clampMaxTokens(value, effectiveModel) : value)
}

// SPEC-014 §5/FR-CFG-010: a dismissible side sheet (Base UI `Dialog`, so it already brings the
// focus trap, `Escape` close and focus restoration). Only `visibleFields(profile)` renders
// (FR-CFG-002/004), each dispatched through `OverrideField`.
export function SettingsSheet({
  open,
  onOpenChange,
  profile,
  pendingKeys = new Set(),
  resetPending = false,
  rejectionReasons = [],
  onCommit,
  onDismissRejections,
  onReset,
}: SettingsSheetProps) {
  const overridden = overriddenKeys(profile)
  const effectiveModel = resolveEffectiveModel(profile)

  // FR-CFG-005a: a typed max_tokens above the live cap is clamped before it is ever sent.
  const handleCommit = commitWithCap(onCommit, effectiveModel)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{MODEL_CONFIG_COPY.sheet.title}</SheetTitle>
          <SheetDescription>{MODEL_CONFIG_COPY.sheet.description}</SheetDescription>
        </SheetHeader>
        <div className="grid gap-4 overflow-y-auto px-4 pb-4">
          <RejectionAlert
            reasons={rejectionReasons}
            {...(onDismissRejections === undefined ? {} : { onDismiss: onDismissRejections })}
          />
          {visibleFields(profile).map((key) => (
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
          {onReset === undefined ? null : <ResetButton disabled={overridden.length === 0} pending={resetPending} onReset={onReset} />}
        </div>
      </SheetContent>
    </Sheet>
  )
}
