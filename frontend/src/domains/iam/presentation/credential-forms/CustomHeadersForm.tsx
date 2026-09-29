import { CredentialTextField } from '@/domains/iam/presentation/credential-forms/CredentialTextField'
import type { CredentialFormProps } from '@/domains/iam/presentation/credential-forms/credential-form'

export function CustomHeadersForm(props: CredentialFormProps) {
  return (
    <div className="grid gap-3">
      <CredentialTextField {...props} field="headers" multiline />
    </div>
  )
}
