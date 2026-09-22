import { z } from 'zod'

import type { Capabilities } from '@/domains/iam/domain/capabilities'

export const capabilitiesDtoSchema = z.object({
  is_admin: z.boolean(),
  anonymous: z.boolean(),
  token_usage_enabled: z.boolean(),
})

export const toCapabilities = (value: unknown): Capabilities | null => {
  const parsed = capabilitiesDtoSchema.safeParse(value)

  if (!parsed.success) {
    return null
  }

  return {
    isAdmin: parsed.data.is_admin,
    isAnonymousAdmin: parsed.data.anonymous,
    tokenUsageEnabled: parsed.data.token_usage_enabled,
  }
}