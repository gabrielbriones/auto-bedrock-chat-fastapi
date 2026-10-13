import { useId, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { IAM_COPY } from '@/shared/copy/iam'
import { isErr, type Result } from '@/shared/kernel/result'

import {
  initialCredentialKind,
  isKindSelectorHidden,
  type AuthPolicy,
} from '@/domains/iam/domain/auth-policy'
import type { Credential, CredentialKind, FieldErrors } from '@/domains/iam/domain/credential'
import { firstInvalidField } from '@/domains/iam/domain/credential-policy'
import { ApiKeyForm } from '@/domains/iam/presentation/credential-forms/ApiKeyForm'
import { BasicAuthForm } from '@/domains/iam/presentation/credential-forms/BasicAuthForm'
import { BearerTokenForm } from '@/domains/iam/presentation/credential-forms/BearerTokenForm'
import { CredentialKindSelect } from '@/domains/iam/presentation/credential-forms/CredentialKindSelect'
import { CustomHeadersForm } from '@/domains/iam/presentation/credential-forms/CustomHeadersForm'
import { OAuth2Form } from '@/domains/iam/presentation/credential-forms/OAuth2Form'
import { SsoLoginPanel } from '@/domains/iam/presentation/credential-forms/SsoLoginPanel'
import {
  fieldDomId,
  type CredentialFormProps,
} from '@/domains/iam/presentation/credential-forms/credential-form'
import { useCredentialDraft, type CredentialDraft } from '@/domains/iam/presentation/useCredentialDraft'
import { useInertAppRoot } from '@/domains/iam/presentation/useInertAppRoot'

export type AuthDialogProps = {
  readonly policy: AuthPolicy
  readonly open: boolean
  /** `?auth_method=` pre-selection (FR-IAM-007); never carries a credential value. */
  readonly preselectedKind?: CredentialKind | null | undefined
  readonly submitting: boolean
  readonly onSubmit: (credential: Credential) => void
  readonly onSkip: () => void
  readonly onSsoLogin: () => void
}

const renderForm = (kind: CredentialKind, props: CredentialFormProps, onSsoLogin: () => void) => {
  switch (kind) {
    case 'bearer_token':
      return <BearerTokenForm {...props} />
    case 'basic_auth':
      return <BasicAuthForm {...props} />
    case 'api_key':
      return <ApiKeyForm {...props} />
    case 'oauth2_client_credentials':
      return <OAuth2Form {...props} />
    case 'custom':
      return <CustomHeadersForm {...props} />
    case 'sso':
      return <SsoLoginPanel onLogin={onSsoLogin} disabled={props.disabled} />
  }
}

type SubmitInput = {
  readonly kind: CredentialKind | null
  readonly submitting: boolean
  readonly idPrefix: string
  readonly validate: () => Result<Credential, FieldErrors> | null
  readonly onSubmit: (credential: Credential) => void
  readonly onSsoLogin: () => void
}

const submitCredential = ({
  kind,
  submitting,
  idPrefix,
  validate,
  onSubmit,
  onSsoLogin,
}: SubmitInput) => {
  if (kind === null || submitting) {
    return
  }

  if (kind === 'sso') {
    onSsoLogin()
    return
  }

  const result = validate()

  if (result === null) {
    return
  }

  if (isErr(result)) {
    // The inputs are already mounted, so the first invalid one can be focused without waiting
    // for the error state to render.
    const invalid = firstInvalidField(kind, result.error)
    if (invalid !== null) {
      document.getElementById(fieldDomId(idPrefix, invalid))?.focus()
    }
    return
  }

  onSubmit(result.value)
}

const AuthDialogFooter = ({
  policy,
  kind,
  submitting,
  onSkip,
}: {
  readonly policy: AuthPolicy
  readonly kind: CredentialKind | null
  readonly submitting: boolean
  readonly onSkip: () => void
}) => (
  <DialogFooter>
    {policy.required ? null : (
      <Button type="button" variant="outline" onClick={onSkip}>
        {IAM_COPY.dialog.skip}
      </Button>
    )}
    {kind === 'sso' ? null : (
      <Button type="submit" disabled={submitting || kind === null}>
        {submitting ? IAM_COPY.dialog.submitting : IAM_COPY.dialog.submit}
      </Button>
    )}
  </DialogFooter>
)

const AuthDialogForm = ({
  policy,
  submitting,
  idPrefix,
  controls,
  onSubmit,
  onSkip,
  onSsoLogin,
}: {
  readonly policy: AuthPolicy
  readonly submitting: boolean
  readonly idPrefix: string
  readonly controls: CredentialDraft
  readonly onSubmit: (event: FormEvent) => void
  readonly onSkip: () => void
  readonly onSsoLogin: () => void
}) => {
  const { kind, draft, errors, changeKind, changeField } = controls

  return (
    <form onSubmit={onSubmit} className="grid gap-4" noValidate>
      <DialogHeader>
        <DialogTitle>{IAM_COPY.dialog.title}</DialogTitle>
        <DialogDescription>{IAM_COPY.dialog.description}</DialogDescription>
      </DialogHeader>

      {isKindSelectorHidden(policy) ? null : (
        <CredentialKindSelect
          labelId={`${idPrefix}-kind-label`}
          triggerId={`${idPrefix}-kind`}
          policy={policy}
          kind={kind}
          disabled={submitting}
          onChange={changeKind}
        />
      )}

      {kind === null
        ? null
        : renderForm(
            kind,
            { idPrefix, draft, errors, disabled: submitting, onFieldChange: changeField },
            onSsoLogin,
          )}

      <p className="text-xs text-muted-foreground">{IAM_COPY.dialog.info}</p>

      <AuthDialogFooter policy={policy} kind={kind} submitting={submitting} onSkip={onSkip} />
    </form>
  )
}

export function AuthDialog({
  policy,
  open,
  preselectedKind,
  submitting,
  onSubmit,
  onSkip,
  onSsoLogin,
}: AuthDialogProps) {
  const controls = useCredentialDraft(preselectedKind ?? initialCredentialKind(policy))
  const idPrefix = useId()

  useInertAppRoot(open)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    submitCredential({
      kind: controls.kind,
      submitting,
      idPrefix,
      validate: controls.validate,
      onSubmit,
      onSsoLogin,
    })
  }

  return (
    <Dialog
      open={open}
      // FR-IAM-013 / FR-IAM-008: when auth is required there is no way out — no Escape, no
      // outside press, and no Skip control to find.
      disablePointerDismissal={policy.required}
      onOpenChange={(next) => {
        if (!next && !policy.required) {
          onSkip()
        }
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <AuthDialogForm
          policy={policy}
          submitting={submitting}
          idPrefix={idPrefix}
          controls={controls}
          onSubmit={submit}
          onSkip={onSkip}
          onSsoLogin={onSsoLogin}
        />
      </DialogContent>
    </Dialog>
  )
}
