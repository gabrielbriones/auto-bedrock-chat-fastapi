import { Builder, type WebDriver } from 'selenium-webdriver'
import chrome from 'selenium-webdriver/chrome.js'

// One Chrome driver per test file; headless by default since CI has no display server
// (STD-002 §1). Set HEADED=1 locally to watch the browser drive the tests.
export async function buildChromeDriver(extraArgs: readonly string[] = []): Promise<WebDriver> {
  const options = new chrome.Options()
  options.addArguments(
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--window-size=1280,900',
    ...extraArgs,
  )
  if (process.env.HEADED !== '1') {
    options.addArguments('--headless=new')
  }

  return new Builder().forBrowser('chrome').setChromeOptions(options).build()
}
