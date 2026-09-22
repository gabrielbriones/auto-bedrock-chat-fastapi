import { CredentialTextField } from '@/domains/iam/presentation/credential-forms/CredentialTextField'
import type { CredentialFormProps } from '@/domains/iam/presentation/credential-forms/credential-form'

export function OAuth2Form(props: CredentialFormProps) {
  return (
    <div className="grid gap-3">
      <CredentialTextField {...props} field="clientId" />
      <CredentialTextField {...props} field="clientSecret" secret />
      <CredentialTextField {...props} field="tokenUrl" />
      <CredentialTextField {...props} field="scope" />
    </div>
  )
}
