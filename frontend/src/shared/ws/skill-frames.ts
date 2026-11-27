import { z } from 'zod'

const skillFrame = <Type extends string>(type: Type) =>
  z.object({ type: z.literal(type), timestamp: z.string().min(1) }).strict()

// A `/<skill> ...` chat message loaded its skill (the turn follows) or was refused (no turn runs).
export const skillLoadedFrameSchema = skillFrame('skill_loaded').extend({
  name: z.string(),
  revision: z.number(),
  message_id: z.string(),
  conversation_id: z.string().nullable(),
})

export const skillErrorFrameSchema = skillFrame('skill_error').extend({ code: z.string(), message: z.string() })

// The reply to a `skill_catalog` request; items are parsed by the skills context's own DTO.
export const skillCatalogFrameSchema = skillFrame('skill_catalog').extend({
  items: z.array(z.unknown()),
  error: z.object({ code: z.string(), message: z.string() }).strict().optional(),
})

export const skillFrameSchemas = [skillLoadedFrameSchema, skillErrorFrameSchema, skillCatalogFrameSchema] as const
