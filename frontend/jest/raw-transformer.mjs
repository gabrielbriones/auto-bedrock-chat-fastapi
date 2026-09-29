// Stands in for Vite's `?raw` import suffix: the file's text becomes the module's default export.
export default {
  process(src) {
    return { code: `module.exports = ${JSON.stringify(src)};` }
  },
}
