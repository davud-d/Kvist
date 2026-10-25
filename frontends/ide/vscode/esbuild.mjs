import { build, context } from "esbuild"

const watch = process.argv.includes("--watch")

const host = {
  entryPoints: ["src/extension.ts"],
  outfile: "out/extension.js",
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  external: ["vscode"],
  // The Ink app's core.ts is bundled here too; its import.meta.url only feeds the
  // source-checkout fallback that the extension never takes.
  logOverride: { "empty-import-meta": "silent" },
  sourcemap: true,
}

const webview = {
  entryPoints: ["webview/main.tsx"],
  outfile: "out/webview.js",
  bundle: true,
  platform: "browser",
  format: "iife",
  target: "chrome120",
  loader: { ".svg": "dataurl" },
  jsx: "automatic",
  jsxImportSource: "preact",
  sourcemap: true,
}

if (watch) {
  for (const config of [host, webview]) await (await context(config)).watch()
} else {
  await Promise.all([build(host), build(webview)])
}
