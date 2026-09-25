import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import type { WebDriver } from 'selenium-webdriver'
import pixelmatch from 'pixelmatch'
import { PNG } from 'pngjs'

const SNAPSHOT_DIR = path.resolve(import.meta.dirname, '../__screenshots__')

/**
 * Captures a screenshot and compares its pixels against a committed baseline,
 * writing the baseline on first run. Anti-aliasing drift is ignored, but any
 * meaningful pixel difference still fails the test.
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

  const actualImage = PNG.sync.read(current)
  const baselineImage = PNG.sync.read(baseline)
  const sameDimensions = actualImage.width === baselineImage.width && actualImage.height === baselineImage.height
  const differentPixels = sameDimensions
    ? pixelmatch(actualImage.data, baselineImage.data, null, actualImage.width, actualImage.height, { threshold: 0.1 })
    : 1

  if (differentPixels !== 0) {
    const actualPath = path.join(tmpdir(), `${name}.actual.png`)
    await writeFile(actualPath, current)
    throw new Error(
      `Screenshot "${name}" differs by ${differentPixels} pixels from ${screenshotPath}; ` +
        `review the capture at ${actualPath} before updating the baseline.`,
    )
  }
}
