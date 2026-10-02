import { readFile } from 'node:fs/promises'
import path from 'node:path'

// Production Vite assets are relative; the deployed SPA injects this base into its index.
export const readSpaFile = async (dist: string, relativePath: string): Promise<Buffer> => {
  const body = await readFile(path.join(dist, relativePath))
  if (relativePath !== 'index.html') return body
  return Buffer.from(body.toString('utf8').replace('<head>', '<head><base href="/chat/ui/">'))
}
