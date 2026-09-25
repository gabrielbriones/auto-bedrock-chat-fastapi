import { createTransformer } from '@swc/jest'

// One SWC transformer per syntax: `tsx: true` on a `.ts` file would misparse generic arrows
// (`<T>(x: T) => x`) as JSX, so the parser is chosen from the extension rather than shared.
const parserFor = (filename) => {
  if (filename.endsWith('.tsx')) return { syntax: 'typescript', tsx: true }
  if (filename.endsWith('.ts') || filename.endsWith('.mts')) return { syntax: 'typescript', tsx: false }
  return { syntax: 'ecmascript', jsx: filename.endsWith('.jsx') }
}

const transformers = new Map()

const transformerFor = (filename) => {
  const parser = parserFor(filename)
  const key = JSON.stringify(parser)
  let transformer = transformers.get(key)
  if (transformer === undefined) {
    transformer = createTransformer({
      module: { type: 'es6' },
      jsc: {
        parser,
        target: 'es2023',
        transform: { react: { runtime: 'automatic' } },
      },
    })
    transformers.set(key, transformer)
  }
  return transformer
}

// Vite exposes build-time configuration as `import.meta.env`; under Jest there is no bundler, so
// the same reads are pointed at `process.env` (set by tests via `process.env.VITE_*`).
const rewriteViteEnv = (src) => src.replaceAll('import.meta.env', 'process.env')

export default {
  process(src, filename, options) {
    return transformerFor(filename).process(rewriteViteEnv(src), filename, options)
  },
  processAsync(src, filename, options) {
    return transformerFor(filename).processAsync(rewriteViteEnv(src), filename, options)
  },
  getCacheKey(src, filename, ...rest) {
    return transformerFor(filename).getCacheKey(rewriteViteEnv(src), filename, ...rest)
  },
}
