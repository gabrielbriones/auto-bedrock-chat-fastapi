import { expect } from 'chai'
import type { WebDriver } from 'selenium-webdriver'

import { assertNoAxeViolations } from './helpers/axe.js'
import { matchScreenshot } from './helpers/visual-snapshot.js'
import { buildChromeDriver } from './helpers/webdriver.js'

const SMOKE_PAGE = `data:text/html,${encodeURIComponent(
  '<!doctype html><html lang="en"><head><title>E2E harness smoke</title></head>' +
    '<body><main><h1>E2E harness smoke</h1><p>Selenium, axe and screenshot helpers are wired up.</p></main></body></html>',
)}`

// Task 07 exit criterion: one smoke test per runner proving the harness works end to end.
// The real journeys (E1…E15, STD-002 §3.4) are out of scope here.
describe('E2E harness smoke', function () {
  this.timeout(60_000)

  let driver: WebDriver

  before(async () => {
    driver = await buildChromeDriver()
  })

  after(async () => {
    await driver?.quit()
  })

  it('drives a page, scans it with axe, and captures a screenshot', async () => {
    await driver.get(SMOKE_PAGE)

    expect(await driver.getTitle()).to.equal('E2E harness smoke')

    await assertNoAxeViolations(driver)
    await matchScreenshot(driver, 'harness-smoke')
  })
})
