#!/usr/bin/env bun
// The entry the launcher runs. Solid's JSX transform is a Bun plugin; registering it here
// (instead of through a bunfig.toml in the working directory) lets the interface start from
// any folder, which is where the launcher starts it.
import "@opentui/solid/preload"

await import("./cli.tsx")
