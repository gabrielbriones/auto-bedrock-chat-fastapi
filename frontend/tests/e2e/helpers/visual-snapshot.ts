import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import type { WebDriver } from 'selenium-webdriver'

const SNAPSHOT_DIR = path.resolve(import.meta.dirname, '../__screenshots__')

/**
 * Captures a screenshot and compares it byte-for-byte against a committed baseline,
 * writing the baseline on first run. This is a capture/compare scaffold only — pixel-diffing
 * with a tolerance (for anti-aliasing/font-rendering drift) is deferred to the visual-regression
 * task that consumes this helper (STD-002 §1's "Visual" row).
 */
export async function matchScreenshot(driver: WebDriver, name: string): Promise<void> {
  await mkdir(SNAPSHOT_DIR, { recursive: true })
  const screenshotPath = path.join(SNAPSHOT_DIR, `${name}.png`)
  const current = Buffer.from(await driver.takeScreenshot(), 'base64')

  const baseline = await readFile(screenshotPath).catch(() => undefined)
  if (baseline === undefined) {
    await writeFile(screenshotPath, current)
    return
  }

  if (!current.equals(baseline)) {
    throw new Error(
      `Screenshot "${name}" does not match its baseline at ${screenshotPath}. ` +
        'Delete the file to record a new baseline if the change is intentional.',
    )
  }
}
