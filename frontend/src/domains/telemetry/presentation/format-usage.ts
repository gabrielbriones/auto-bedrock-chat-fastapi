const numberFormatter = new Intl.NumberFormat()
const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})

export const formatUsageNumber = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : numberFormatter.format(value)

export const formatUsageInstant = (epochMilliseconds: number | null | undefined): string =>
  epochMilliseconds === null || epochMilliseconds === undefined
    ? '—'
    : dateTimeFormatter.format(epochMilliseconds)

export const truncateSessionId = (value: string | null | undefined): string => {
  if (value === null || value === undefined) return '—'
  return value.length > 36 ? `${value.slice(0, 33)}...` : value
}