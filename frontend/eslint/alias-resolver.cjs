// eslint-plugin-boundaries resolves imports through `eslint-module-utils`, which knows nothing
// about the `@/*` tsconfig path. Without this every project import looks like an unresolvable
// external package and no layer rule can fire. Loaded by `require`, hence CommonJS.
const fs = require('node:fs')
const path = require('node:path')

const EXTENSIONS = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.json', '.css']

function resolveFile(base) {
  if (fs.existsSync(base) && fs.statSync(base).isFile()) {
    return base
  }

  for (const extension of EXTENSIONS) {
    const candidate = `${base}${extension}`
    if (fs.existsSync(candidate)) {
      return candidate
    }
  }

  return null
}

module.exports = {
  interfaceVersion: 2,

  /**
   * @param {string} source import specifier
   * @param {string} _file importing file
   * @param {{ srcDir: string, prefix?: string }} config
   */
  resolve(source, _file, config) {
    const prefix = config.prefix ?? '@/'
    if (!source.startsWith(prefix)) {
      return { found: false }
    }

    // Vite query suffixes (`?raw`) are not part of the path.
    const [specifier] = source.slice(prefix.length).split('?')
    const resolved = resolveFile(path.join(config.srcDir, specifier ?? ''))

    return resolved === null ? { found: false } : { found: true, path: resolved }
  },
}
