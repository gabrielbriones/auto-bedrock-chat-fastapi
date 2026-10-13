import type { WebDriver } from 'selenium-webdriver'

// Emulates the `forced-colors: active` media feature via CDP, matching what Chromium's
// "Emulate CSS media feature forced-colors" DevTools option does (NFR-A11Y-007).
export async function enableForcedColors(driver: WebDriver): Promise<void> {
  const connection = await driver.createCDPConnection('page')
  await connection.send('Emulation.setEmulatedMedia', {
    features: [{ name: 'forced-colors', value: 'active' }],
  })
}
