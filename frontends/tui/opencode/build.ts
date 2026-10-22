// Compiles the interface into one executable (`bun build --compile`) so the Kvist binary can carry it.
import solidPlugin from "@opentui/solid/bun-plugin"

const out = process.argv[2]
if (!out) throw new Error("usage: bun build.ts <output file>")

const result = await Bun.build({
  entrypoints: ["./src/cli.tsx"],
  plugins: [solidPlugin],
  compile: { outfile: out },
})
if (!result.success) {
  for (const log of result.logs) console.error(log)
  process.exit(1)
}
