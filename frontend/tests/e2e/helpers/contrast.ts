import type { WebDriver } from 'selenium-webdriver'

export type ContrastPair = {
  readonly name: string
  readonly foreground: string
  readonly background: string
  readonly minRatio: number
}

// NFR-A11Y-004: text contrast >= 4.5:1 in both themes. Pairs mirror the semantic tokens in
// src/index.css that components actually use for text-on-fill combinations.
export const TEXT_CONTRAST_PAIRS: readonly ContrastPair[] = [
  { name: 'foreground on background', foreground: '--foreground', background: '--background', minRatio: 4.5 },
  { name: 'card-foreground on card', foreground: '--card-foreground', background: '--card', minRatio: 4.5 },
  { name: 'popover-foreground on popover', foreground: '--popover-foreground', background: '--popover', minRatio: 4.5 },
  { name: 'primary-foreground on primary', foreground: '--primary-foreground', background: '--primary', minRatio: 4.5 },
  { name: 'secondary-foreground on secondary', foreground: '--secondary-foreground', background: '--secondary', minRatio: 4.5 },
  { name: 'accent-foreground on accent', foreground: '--accent-foreground', background: '--accent', minRatio: 4.5 },
  { name: 'destructive-foreground on destructive', foreground: '--destructive-foreground', background: '--destructive', minRatio: 4.5 },
  { name: 'muted-foreground on muted', foreground: '--muted-foreground', background: '--muted', minRatio: 4.5 },
  { name: 'muted-foreground on background', foreground: '--muted-foreground', background: '--background', minRatio: 4.5 },
  { name: 'message-user-fg on message-user-bg', foreground: '--message-user-fg', background: '--message-user-bg', minRatio: 4.5 },
  { name: 'message-assistant-fg on message-assistant-bg', foreground: '--message-assistant-fg', background: '--message-assistant-bg', minRatio: 4.5 },
  { name: 'message-system-fg on message-system-bg', foreground: '--message-system-fg', background: '--message-system-bg', minRatio: 4.5 },
  { name: 'code-fg on code-bg', foreground: '--code-fg', background: '--code-bg', minRatio: 4.5 },
]

// NFR-A11Y-004: large text and true UI-component boundaries (WCAG 1.4.11) need >= 3:1.
// `--border` / `--tool-border` are decorative dividers, not required boundaries, so they are
// intentionally excluded here — only tokens that are a control's sole boundary cue are checked.
export const BOUNDARY_CONTRAST_PAIRS: readonly ContrastPair[] = [
  { name: 'input on background', foreground: '--input', background: '--background', minRatio: 3 },
  { name: 'ring on background', foreground: '--ring', background: '--background', minRatio: 3 },
]

// Resolves each pair's computed colors in the live document (so color-mix()/oklch ramps are
// resolved by the browser itself) and returns the WCAG relative-luminance contrast ratio.
//
// getComputedStyle() on a color-mix()-derived value can serialize as oklch(...) rather than
// rgb(...) (observed on Chrome for the `in oklch` ramps in src/index.css), so colors are
// normalized to sRGB bytes via a 1x1 canvas instead of regex-parsing the serialized string.
// Some tokens (e.g. dark-theme `--accent`, `--tool-bg`) are semi-transparent tints meant to sit
// over `--background`, so every color is composited onto the page background before measuring.
export async function measureContrastRatios(
  driver: WebDriver,
  pairs: readonly ContrastPair[],
): Promise<Record<string, number>> {
  return driver.executeScript<Record<string, number>>(
    `
    const pairs = arguments[0]
    const probe = document.createElement('div')
    probe.style.position = 'fixed'
    probe.style.top = '-9999px'
    document.body.appendChild(probe)

    const canvas = document.createElement('canvas')
    canvas.width = 1
    canvas.height = 1
    const ctx = canvas.getContext('2d')

    // Composites a stack of colors in painting order (source-over), so translucent tokens
    // resolve to the same effective color a user would actually see rendered on screen. The
    // first color in the stack (the page background) is always opaque, so it fully replaces
    // whatever was previously on the canvas.
    const compositeOver = (colors) => {
      for (const color of colors) {
        ctx.fillStyle = color
        ctx.fillRect(0, 0, 1, 1)
      }
      return ctx.getImageData(0, 0, 1, 1).data
    }

    const channel = (value) => (value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4))
    const relativeLuminance = (rgbBytes) =>
      0.2126 * channel(rgbBytes[0] / 255) + 0.7152 * channel(rgbBytes[1] / 255) + 0.0722 * channel(rgbBytes[2] / 255)

    probe.style.color = 'var(--background)'
    probe.style.backgroundColor = 'var(--background)'
    const pageBackground = getComputedStyle(probe).backgroundColor

    const results = {}
    for (const pair of pairs) {
      probe.style.color = 'var(' + pair.foreground + ')'
      probe.style.backgroundColor = 'var(' + pair.background + ')'
      const style = getComputedStyle(probe)
      const effectiveBackground = compositeOver([pageBackground, style.backgroundColor])
      const effectiveForeground = compositeOver([pageBackground, style.backgroundColor, style.color])
      const l1 = relativeLuminance(effectiveForeground)
      const l2 = relativeLuminance(effectiveBackground)
      const lighter = Math.max(l1, l2)
      const darker = Math.min(l1, l2)
      results[pair.name] = (lighter + 0.05) / (darker + 0.05)
    }

    probe.remove()
    return results
    `,
    pairs,
  )
}
