import { existsSync, readdirSync } from 'node:fs'
import path from 'node:path'

import { expect } from '@jest/globals'
import { Builder, Key, WebElement, error as errors, type WebDriver } from 'selenium-webdriver'
import chrome from 'selenium-webdriver/chrome.js'
import type { Command } from 'selenium-webdriver/lib/command.js'

// One Chrome driver per test file; headless by default since CI has no display server
// (STD-002 §1).
//
// Watch mode, for checking what a journey actually does rather than only whether it passes:
//   HEADED=1      opens a visible window and narrates every interaction — in a banner at the top of
//                 the page (current test + the step about to happen, target element outlined) and on
//                 stderr, so a run can also be read back afterwards.
//   E2E_SLOWMO=ms pauses that long before each interaction (default 500 when HEADED=1, else 0).
// `npm run test:e2e:headed` sets both and runs one spec at a time; see README "Verify".
export async function buildChromeDriver(
  extraArgs: readonly string[] = [],
  requireBuild = true,
  pageLoadStrategy: 'normal' | 'none' = 'normal',
): Promise<WebDriver> {
  if (requireBuild) requireBuiltBundle()
  const watch = readWatchOptions()

  const options = new chrome.Options()
  options.setPageLoadStrategy(pageLoadStrategy)
  options.addArguments(
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--window-size=1280,900',
    ...extraArgs,
  )
  if (watch.headed) {
    attachDisplay(options)
  } else {
    options.addArguments('--headless=new')
  }

  let driver: WebDriver
  try {
    driver = await new Builder().forBrowser('chrome').setChromeOptions(options).build()
  } catch (error) {
    if (watch.headed && error instanceof errors.SessionNotCreatedError) {
      throw new Error(
        `HEADED=1: Chrome could not open a window (DISPLAY=${process.env.DISPLAY ?? 'unset'}, ` +
          `WAYLAND_DISPLAY=${process.env.WAYLAND_DISPLAY ?? 'unset'}). Run from a terminal inside the ` +
          'desktop session, or export DISPLAY and XAUTHORITY for it; headless runs are unaffected.',
        { cause: error },
      )
    }
    throw error
  }
  // Headless Chrome reports a light colour scheme; a visible window follows the desktop's. The
  // app picks its theme from that unless a spec stores one, so the scheme is pinned to keep a
  // journey's theme the same on every machine — specs that want dark set it explicitly.
  await (driver as chrome.Driver).sendDevToolsCommand('Emulation.setEmulatedMedia', {
    features: [{ name: 'prefers-color-scheme', value: 'light' }],
  })
  if (watch.headed || watch.slowMo > 0) {
    installNarrator(driver, watch)
  }
  return driver
}

// Chrome exits at once without a display server, and a plain terminal or an IDE task often has
// neither DISPLAY nor WAYLAND_DISPLAY exported even on a desktop. The session's Wayland socket is
// preferred because it needs no X authority file; an X display is used when the shell names one,
// or as the last resort on `:0` with GNOME's Xwayland authority file.
const attachDisplay = (options: chrome.Options): void => {
  const runtimeDir = process.env.XDG_RUNTIME_DIR ?? `/run/user/${process.getuid?.() ?? ''}`
  const entries = existsSync(runtimeDir) ? readdirSync(runtimeDir) : []

  const wayland =
    process.env.WAYLAND_DISPLAY ??
    (process.env.DISPLAY === undefined ? entries.find((name) => /^wayland-\d+$/.test(name)) : undefined)
  if (wayland !== undefined) {
    process.env.WAYLAND_DISPLAY = wayland
    options.addArguments('--ozone-platform=wayland')
    return
  }

  if (process.env.DISPLAY === undefined) {
    if (!existsSync('/tmp/.X11-unix/X0')) {
      throw new Error(
        'HEADED=1 needs a display server, but neither DISPLAY nor WAYLAND_DISPLAY is set and no ' +
          'Wayland or X socket was found. Run from a terminal inside the desktop session.',
      )
    }
    process.env.DISPLAY = ':0'
  }
  if (process.env.XAUTHORITY === undefined) {
    const mutterAuth = entries.find((name) => name.startsWith('.mutter-Xwaylandauth.'))
    if (mutterAuth !== undefined) process.env.XAUTHORITY = path.join(runtimeDir, mutterAuth)
  }
}

/** The narration banner's selector, so screenshots and axe scans can leave it out. */
export const NARRATOR_SELECTOR = '[data-e2e-narrator]'

export type WatchOptions = {
  readonly headed: boolean
  readonly slowMo: number
}

export const readWatchOptions = (): WatchOptions => {
  const headed = process.env.HEADED === '1'
  const parsed = Number.parseInt(process.env.E2E_SLOWMO ?? '', 10)
  const slowMo = Number.isFinite(parsed) ? Math.max(0, parsed) : headed ? 500 : 0
  return { headed, slowMo }
}

// Every journey serves `dist/`; without it the SPA 404s and the failure surfaces as an unrelated
// "element not located" timeout minutes later.
const requireBuiltBundle = (): void => {
  const index = path.resolve(process.cwd(), 'dist', 'index.html')
  if (!existsSync(index)) {
    throw new Error(
      `The e2e journeys drive the built bundle, but ${index} does not exist: run \`npm run build\` first.`,
    )
  }
}

// WebDriver command names (selenium-webdriver/lib/command.js `Name`) that change what is on screen.
const INTERACTIONS = new Set([
  'get',
  'goBack',
  'goForward',
  'refresh',
  'clickElement',
  'clearElement',
  'sendKeysToElement',
  'actions',
  'setWindowRect',
])
const SCREENSHOTS = new Set(['screenshot', 'takeElementScreenshot'])
// The W3C WebDriver element identifier key (selenium-webdriver/lib/webdriver.js `ELEMENT_ID_KEY`).
const ELEMENT_KEY = 'element-6066-11e4-a52e-4f735466cecf'

// Key constants are private-use code points; shown by name so "" reads as ⟨ENTER⟩.
const KEY_NAMES = new Map<string, string>()
for (const [name, value] of Object.entries(Key)) {
  if (typeof value === 'string' && value.length === 1) KEY_NAMES.set(value, name)
}

const describeKeys = (text: string): string =>
  [...text].map((char) => (KEY_NAMES.has(char) ? `⟨${KEY_NAMES.get(char)}⟩` : char)).join('')

type ActionSequence = {
  readonly type?: string
  readonly actions?: readonly { readonly type?: string; readonly value?: string }[]
}

const describeActions = (sequences: unknown): string => {
  const parts: string[] = []
  for (const sequence of Array.isArray(sequences) ? (sequences as ActionSequence[]) : []) {
    if (sequence.type === 'key') {
      const keys = (sequence.actions ?? [])
        .filter((action) => action.type === 'keyDown')
        .map((action) => describeKeys(action.value ?? ''))
        .join('')
      if (keys !== '') parts.push(`Press ${keys}`)
    } else if (sequence.type === 'pointer') {
      parts.push('Move or press the pointer')
    }
  }
  return parts.length > 0 ? parts.join('; ') : 'Perform input actions'
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

const BANNER_SCRIPT = `
  const [testName, line] = arguments
  let banner = document.querySelector('[data-e2e-narrator]')
  if (!banner) {
    banner = document.createElement('div')
    banner.setAttribute('data-e2e-narrator', '')
    banner.setAttribute('aria-hidden', 'true')
    banner.style.cssText =
      'position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:2147483647;max-width:92vw;' +
      'padding:6px 14px;border-radius:0 0 10px 10px;background:rgba(20,20,20,.88);color:#fff;' +
      'font:13px/1.45 system-ui,sans-serif;pointer-events:none;box-shadow:0 2px 8px rgba(0,0,0,.4);' +
      'white-space:pre-wrap'
    const test = document.createElement('div')
    test.setAttribute('data-e2e-test', '')
    test.style.fontWeight = '600'
    const step = document.createElement('div')
    step.setAttribute('data-e2e-step', '')
    step.style.color = '#ffd166'
    banner.append(test, step)
    document.body.append(banner)
  }
  banner.querySelector('[data-e2e-test]').textContent = testName
  banner.querySelector('[data-e2e-step]').textContent = line
`

const BANNER_VISIBILITY_SCRIPT = `
  const banner = document.querySelector('[data-e2e-narrator]')
  if (banner) banner.style.display = arguments[0] ? '' : 'none'
`

/**
 * Runs `action` with the narration banner hidden. Screenshots get this automatically; an axe
 * scan needs it explicitly, because excluding the banner from the scan only stops axe auditing
 * the banner itself — it still overlaps whatever sits under it and so becomes that element's
 * background for the colour-contrast rule.
 */
export const withNarratorHidden = async <T>(driver: WebDriver, action: () => Promise<T>): Promise<T> => {
  const setVisible = (visible: boolean) =>
    driver.executeScript(BANNER_VISIBILITY_SCRIPT, visible).catch(() => undefined)
  await setVisible(false)
  try {
    return await action()
  } finally {
    await setVisible(true)
  }
}

const DESCRIBE_ELEMENT_SCRIPT = `
  const element = arguments[0]
  if (!element) return 'element'
  const tag = element.tagName.toLowerCase()
  const label =
    element.getAttribute('aria-label') ||
    element.getAttribute('placeholder') ||
    (element.labels && element.labels[0] && element.labels[0].textContent) ||
    element.textContent ||
    ''
  const text = label.trim().replace(/\\s+/g, ' ').slice(0, 48)
  return text ? tag + ' "' + text + '"' : tag
`

const HIGHLIGHT_SCRIPT = `
  const [element, on] = arguments
  if (!element) return
  if (on) {
    element.dataset.e2eOutline = element.style.outline
    element.style.outline = '3px solid #ff2d95'
    element.style.outlineOffset = '2px'
  } else {
    element.style.outline = element.dataset.e2eOutline || ''
    element.style.outlineOffset = ''
    delete element.dataset.e2eOutline
  }
`

// Wraps the driver's command executor so the narration rides on the commands the specs already
// issue: no spec has to call anything, and nothing runs at all outside watch mode.
const installNarrator = (driver: WebDriver, watch: WatchOptions): void => {
  const executor = driver.getExecutor()
  const execute = executor.execute.bind(executor)
  let step = 0
  let lastLine = ''
  let narrating = false

  // The narrator's own scripts go through the same executor; they must not narrate themselves, and
  // a page mid-navigation may refuse them, which is never worth failing the spec over.
  const run = async <T>(script: string, ...args: unknown[]): Promise<T | undefined> => {
    narrating = true
    try {
      return await driver.executeScript<T>(script, ...args)
    } catch (error) {
      process.stderr.write(`  [e2e] narration skipped: ${error instanceof Error ? error.message : String(error)}\n`)
      return undefined
    } finally {
      narrating = false
    }
  }

  const currentTest = (): string => expect.getState().currentTestName ?? ''
  const showBanner = () => run(BANNER_SCRIPT, currentTest(), lastLine)

  const narrate = async (description: string): Promise<void> => {
    step += 1
    lastLine = `${step}. ${description}`
    process.stderr.write(`  [e2e] ${currentTest()} — ${lastLine}\n`)
    await showBanner()
  }

  // Element commands carry their target as the W3C element reference `{ "<ELEMENT_KEY>": id }`.
  const targetOf = (command: Command): WebElement => {
    const reference = command.getParameter('id') as unknown
    const id =
      typeof reference === 'object' && reference !== null
        ? String((reference as Record<string, unknown>)[ELEMENT_KEY])
        : String(reference)
    return new WebElement(driver, id)
  }

  const describeTarget = async (command: Command): Promise<string> =>
    (await run<string>(DESCRIBE_ELEMENT_SCRIPT, targetOf(command))) ?? 'element'

  const describeCommand = async (command: Command): Promise<string> => {
    switch (command.getName()) {
      case 'get':
        return `Open ${String(command.getParameter('url'))}`
      case 'goBack':
        return 'Go back'
      case 'goForward':
        return 'Go forward'
      case 'refresh':
        return 'Reload the page'
      case 'clickElement':
        return `Click ${await describeTarget(command)}`
      case 'clearElement':
        return `Clear ${await describeTarget(command)}`
      case 'sendKeysToElement':
        return `Type ${describeKeys(String(command.getParameter('text') ?? ''))} into ${await describeTarget(command)}`
      case 'actions':
        return describeActions(command.getParameter('actions'))
      case 'setWindowRect':
        return `Resize the window to ${String(command.getParameter('width'))}×${String(command.getParameter('height'))}`
      default:
        return command.getName()
    }
  }

  executor.execute = async (command: Command): Promise<unknown> => {
    const name = command.getName()
    if (narrating) return execute(command)

    if (name === 'quit') {
      // Leave the final state on screen for a moment instead of closing the window mid-glance.
      if (watch.headed) await sleep(Math.max(1_500, watch.slowMo * 3))
      return execute(command)
    }

    if (SCREENSHOTS.has(name)) {
      await run(BANNER_VISIBILITY_SCRIPT, false)
      try {
        return await execute(command)
      } finally {
        await run(BANNER_VISIBILITY_SCRIPT, true)
      }
    }

    if (!INTERACTIONS.has(name)) return execute(command)

    // Resolved up front: the HTTP executor splices path parameters such as `id` out of the command
    // while building the request, so after `execute` the target is no longer on the command.
    const target = command.getParameter('id') === undefined ? null : targetOf(command)
    await narrate(await describeCommand(command))
    if (target !== null) await run(HIGHLIGHT_SCRIPT, target, true)
    await sleep(watch.slowMo)
    try {
      return await execute(command)
    } finally {
      if (target !== null) await run(HIGHLIGHT_SCRIPT, target, false)
      // A navigation replaces the document, and the banner with it.
      if (name === 'get') await showBanner()
    }
  }
}
