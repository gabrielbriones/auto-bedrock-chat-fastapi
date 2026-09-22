import { CredentialTextField } from '@/domains/iam/presentation/credential-forms/CredentialTextField'
import type { CredentialFormProps } from '@/domains/iam/presentation/credential-forms/credential-form'

export function BasicAuthForm(props: CredentialFormProps) {
  return (
    <div className="grid gap-3">
      <CredentialTextField {...props} field="username" />
      <CredentialTextField {...props} field="password" secret />
    </div>
  )
}
