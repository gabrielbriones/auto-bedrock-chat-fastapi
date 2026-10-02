import { SparklesIcon } from 'lucide-react'
import { siClaude, siDeepseek, siGooglegemini, siMeta, siMistralai, type SimpleIcon } from 'simple-icons'

// Keyed by a lowercase substring of the catalog's free-text `provider`.
const LOGOS: readonly (readonly [string, SimpleIcon])[] = [
  ['anthropic', siClaude],
  ['claude', siClaude],
  ['meta', siMeta],
  ['mistral', siMistralai],
  ['google', siGooglegemini],
  ['deepseek', siDeepseek],
]

export function ProviderLogo({ provider }: { readonly provider: string | null }) {
  const name = provider?.toLowerCase() ?? ''
  const icon = name === '' ? undefined : LOGOS.find(([key]) => name.includes(key))?.[1]

  if (icon === undefined) {
    return <SparklesIcon aria-hidden />
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill={`#${icon.hex}`}>
      <path d={icon.path} />
    </svg>
  )
}
