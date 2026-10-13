import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { describe, expect, it } from '@jest/globals'

import { readSpaFile } from './spa-file.js'

describe('E2E SPA bundle serving', () => {
  it('injects the production UI base into HTML but does not alter bundled assets', async () => {
    const dist = await mkdtemp(path.join(tmpdir(), 'autochat-spa-'))
    try {
      await mkdir(path.join(dist, 'assets'))
      const index = '<!doctype html><html><head><script src="./assets/index.js"></script></head></html>'
      await writeFile(path.join(dist, 'index.html'), index)
      await writeFile(path.join(dist, 'assets/index.js'), 'console.log("hello")')

      const html = (await readSpaFile(dist, 'index.html')).toString('utf8')
      expect(html).toContain('<head><base href="/chat/ui/">')
      expect(html).toContain('src="./assets/index.js"')
      expect(await readSpaFile(dist, 'assets/index.js')).toEqual(await readFile(path.join(dist, 'assets/index.js')))
    } finally {
      await rm(dist, { recursive: true, force: true })
    }
  })
})
