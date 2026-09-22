import { CredentialTextField } from '@/domains/iam/presentation/credential-forms/CredentialTextField'
import type { CredentialFormProps } from '@/domains/iam/presentation/credential-forms/credential-form'

export function ApiKeyForm(props: CredentialFormProps) {
  return (
    <div className="grid gap-3">
      <CredentialTextField {...props} field="apiKey" secret />
      <CredentialTextField {...props} field="header" />
    </div>
  )
}
