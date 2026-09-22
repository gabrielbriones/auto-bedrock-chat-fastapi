import { describe, expect, it } from 'vitest'
// Vite's `?raw` import, so the assertion runs against the document the build actually ships.
import indexHtml from '../../../index.html?raw'

import {
  THEME_BOOTSTRAP_SCRIPT,
  THEME_BOOTSTRAP_SCRIPT_CSP_HASH,
} from '@/app/theme/theme-bootstrap'

const toBase64 = (bytes: ArrayBuffer): string =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))

describe('the pre-paint theme bootstrap', () => {
  it('is the script index.html actually ships', () => {
    expect(indexHtml).toContain(`<script>${THEME_BOOTSTRAP_SCRIPT}</script>`)
  })

  // NFR-SEC-006: a strict CSP allow-lists this exact hash, so drift breaks the deployed page.
  it('matches the documented CSP hash', async () => {
    const digest = await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(THEME_BOOTSTRAP_SCRIPT),
    )

    expect(`sha256-${toBase64(digest)}`).toBe(THEME_BOOTSTRAP_SCRIPT_CSP_HASH)
  })

  it('is the only inline script in the document', () => {
    const inlineScripts = indexHtml.match(/<script(?![^>]*\ssrc=)[^>]*>/g) ?? []

    expect(inlineScripts).toHaveLength(1)
  })
})
