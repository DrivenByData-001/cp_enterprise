import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { gzipSync } from 'node:zlib'
import assert from 'node:assert/strict'

const root = resolve('dist')
const html = readFileSync(resolve(root, 'index.html'), 'utf8')
const files = new Set()
function visit(file) {
  if (files.has(file)) return
  files.add(file)
  const source = readFileSync(file, 'utf8')
  // Include static dependencies; route import(...) chunks are intentionally deferred.
  for (const match of source.matchAll(/(?:from\s*|import\s*)["'](\.\/[^"']+\.js)["']/g)) {
    visit(resolve(dirname(file), match[1]))
  }
}
for (const match of html.matchAll(/(?:src|href)=["']([^"']+\.js)["']/g)) {
  visit(resolve(root, match[1].replace(/^\//, '')))
}
assert(files.size > 0, 'No initial JavaScript entry was found')
const gzipBytes = [...files].reduce((sum, file) => sum + gzipSync(readFileSync(file)).length, 0)
console.log(JSON.stringify({ initialChunks: files.size, initialGzipBytes: gzipBytes, budgetBytes: 150000 }))
assert(gzipBytes <= 150000, 'Initial JavaScript exceeded 150 kB gzip; check for eager analytical routes')
