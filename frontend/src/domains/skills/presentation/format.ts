import type { Instant } from '@/shared/kernel/instant'
import { SKILLS_COPY } from '@/shared/copy/skills'

const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

export const formatInstant = (instant: Instant | null): string =>
  instant === null ? SKILLS_COPY.list.notAvailable : formatter.format(instant.epochMilliseconds)
