import { z } from 'zod'

import type { Capabilities } from '@/domains/iam/domain/capabilities'

export const capabilitiesDtoSchema = z.object({
  is_admin: z.boolean(),
  anonymous: z.boolean(),
  token_usage_enabled: z.boolean(),
  // Added to the probe alongside the KB source routes; a backend from before that answers
  // without it, which must read as "not enabled" rather than lock every admin out.
  kb_source_ingestion_enabled: z.boolean().default(false),
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
    kbSourceIngestionEnabled: parsed.data.kb_source_ingestion_enabled,
  }
}