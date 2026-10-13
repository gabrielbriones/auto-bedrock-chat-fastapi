import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { AuthPolicy } from '@/domains/iam/domain/auth-policy'
import type { CredentialKind } from '@/domains/iam/domain/credential'
import { IAM_COPY } from '@/shared/copy/iam'

export type CredentialKindSelectProps = {
  readonly labelId: string
  readonly triggerId: string
  readonly policy: AuthPolicy
  readonly kind: CredentialKind | null
  readonly disabled: boolean
  readonly onChange: (kind: CredentialKind) => void
}

export function CredentialKindSelect({
  labelId,
  triggerId,
  policy,
  kind,
  disabled,
  onChange,
}: CredentialKindSelectProps) {
  return (
    <div className="grid gap-2">
      <Label id={labelId} htmlFor={triggerId}>
        {IAM_COPY.dialog.kindLabel}
      </Label>
      <Select
        value={kind}
        onValueChange={(value: CredentialKind | null) => {
          if (value !== null) {
            onChange(value)
          }
        }}
        disabled={disabled}
      >
        <SelectTrigger id={triggerId} aria-labelledby={labelId} className="w-full">
          <SelectValue>
            {kind === null ? IAM_COPY.dialog.kindPlaceholder : IAM_COPY.kinds[kind]}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {policy.supportedKinds.map((supported) => (
            <SelectItem key={supported} value={supported}>
              {IAM_COPY.kinds[supported]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
