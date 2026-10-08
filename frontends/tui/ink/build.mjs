// Bundles the app into dist-ui/kvist-tui.mjs (`npm run build`).
import { build } from "esbuild"
import { readFileSync } from "node:fs"
import { dirname } from "node:path"
import { pathToFileURL } from "node:url"

// Ink 7 caches text measurements in a module-level Map keyed by the full text and never evicts.
// A streamed thought or answer measures a new, longer string on every frame, so the cache keeps
// every prefix of every stream: memory grew quadratically until the kernel killed the process.
// Replaces the module with the same function over a cache capped by total characters.
const CACHE_CHARS = 1_000_000

const BOUNDED_MEASURE_TEXT = `
import widestLine from "widest-line"
const cache = new Map()
let cachedChars = 0
export default function measureText(text) {
  if (text.length === 0) return { width: 0, height: 0 }
  const cached = cache.get(text)
  if (cached) return cached
  const dimensions = { width: widestLine(text), height: text.split("\\n").length }
  if (text.length > ${CACHE_CHARS}) return dimensions
  cache.set(text, dimensions)
  cachedChars += text.length
  for (const [oldest] of cache) {
    if (cachedChars <= ${CACHE_CHARS}) break
    cache.delete(oldest)
    cachedChars -= oldest.length
  }
  return dimensions
}
`

/** esbuild plugin that swaps Ink's unbounded measure-text cache for a size-capped one. */
export const boundedInkMeasureCache = {
  name: "bounded-ink-measure-cache",
  setup(builder) {
    builder.onLoad({ filter: /[\\/]ink[\\/]build[\\/]measure-text\.js$/ }, (args) => {
      const original = readFileSync(args.path, "utf8")
      if (!original.includes("const cache = new Map()")) {
        throw new Error("ink/build/measure-text.js changed: re-check the cache replacement in build.mjs")
      }
      return { contents: BOUNDED_MEASURE_TEXT, resolveDir: dirname(args.path), loader: "js" }
    })
  },
}

export const options = {
  entryPoints: ["cli.tsx"],
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  alias: { "react-devtools-core": "./vendor/react-devtools-core.js" },
  banner: { js: "import { createRequire as __kvistRequire } from 'node:module'; const require = __kvistRequire(import.meta.url);" },
  // Without it React runs its development build, which records a performance.measure entry per
  // component render, props diff included. Node never clears those entries, so every streamed
  // chunk of a thought stayed in memory (1 GB RSS after 30 long turns in a synthetic run).
  // The launcher does not set NODE_ENV, so the bundle must.
  define: { "process.env.NODE_ENV": '"production"' },
  outfile: "dist-ui/kvist-tui.mjs",
  plugins: [boundedInkMeasureCache],
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) await build(options)
