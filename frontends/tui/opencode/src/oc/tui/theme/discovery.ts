import { readdir, readFile } from "node:fs/promises"
import path from "node:path"

export async function discoverThemes(directories: string[]) {
  const result: Record<string, unknown> = {}
  for (const directory of directories) {
    const themeDirectory = path.join(directory, "themes")
    const entries = await readdir(themeDirectory, { withFileTypes: true }).catch((error: unknown) => {
      if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") return []
      return Promise.reject(error)
    })
    const files = entries
      .filter((entry) => (entry.isFile() || entry.isSymbolicLink()) && path.extname(entry.name) === ".json")
      .map((entry) => path.join(themeDirectory, entry.name))
      .sort()
    for (const file of files) {
      // Kvist: "system" is generated from the terminal palette; a file of that name (the core ships one) fights
      // the generated theme in an endless reactive loop that eats memory and freezes the interface.
      if (path.basename(file, ".json") === "system") continue
      result[path.basename(file, ".json")] = JSON.parse(await readFile(file, "utf8")) as unknown
    }
  }
  return result
}
