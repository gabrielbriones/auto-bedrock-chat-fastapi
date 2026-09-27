import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import type { WebDriver } from 'selenium-webdriver'
import pixelmatch from 'pixelmatch'
import { PNG } from 'pngjs'

import { readWatchOptions } from './webdriver.js'

const SNAPSHOT_DIR = path.resolve(import.meta.dirname, '../__screenshots__')

let headedSkipAnnounced = false

/**
 * Captures a screenshot and compares its pixels against a committed baseline,
 * writing the baseline on first run. Anti-aliasing drift is ignored, but any
 * meaningful pixel difference still fails the test.
 *
 * The baselines are captured headless. A headed window loses part of the requested size to
 * Chrome's tab strip and toolbar (and cannot go narrower than Chrome's minimum window width), so
 * its captures can never match and the comparison is skipped in watch mode rather than failing
 * every screenshot spec for a reason that has nothing to do with the page.
 */
export async function matchScreenshot(driver: WebDriver, name: string): Promise<void> {
  if (readWatchOptions().headed) {
    if (!headedSkipAnnounced) {
      headedSkipAnnounced = true
      process.stderr.write('  [e2e] HEADED=1: screenshot baselines are compared headless only; skipping.\n')
    }
    return
  }

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
    ? pixelmatch(actualImage.data, baselineImage.data, undefined, actualImage.width, actualImage.height, { threshold: 0.1 })
    : 0

  if (!sameDimensions || differentPixels !== 0) {
    const actualPath = path.join(tmpdir(), `${name}.actual.png`)
    await writeFile(actualPath, current)
    const difference = sameDimensions
      ? `differs by ${differentPixels} pixels from`
      : `is ${actualImage.width}×${actualImage.height} but the baseline is ` +
        `${baselineImage.width}×${baselineImage.height} at`
    throw new Error(
      `Screenshot "${name}" ${difference} ${screenshotPath}; ` +
        `review the capture at ${actualPath} before updating the baseline.`,
    )
  }
}
