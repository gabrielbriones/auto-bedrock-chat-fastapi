// FR-DS-003 / NFR-SEC-006: the only inline script in the application. It runs before first paint
// so an explicit dark-mode preference never flashes white, and its hash is what a strict CSP
// allow-lists. `theme-bootstrap.test.ts` fails if index.html and this constant drift apart.
export const THEME_STORAGE_KEY = 'ui.theme'

export const THEME_BOOTSTRAP_SCRIPT = `(function(){try{var t=localStorage.getItem('${THEME_STORAGE_KEY}');var d=window.matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.classList.add(t==='light'||t==='dark'?t:(d?'dark':'light'))}catch(e){}})()`

// sha256-<base64>, as it appears in a `script-src` directive.
export const THEME_BOOTSTRAP_SCRIPT_CSP_HASH =
  'sha256-Hnpn9BF4ZiTbsSjrTmatLW83O/OrhwjQBkhylUNvX0Q='
