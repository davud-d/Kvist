// Ink queries keyboard capabilities in its constructor, before useInput's
// effects enable raw mode. Disable terminal echo before that first query.
export function renderWithRawInput<T>(stdin: Pick<NodeJS.ReadStream, "isRaw" | "setRawMode">, render: () => T): T {
  const wasRaw = stdin.isRaw
  stdin.setRawMode(true)
  try {
    // After mounting, Ink owns raw mode and restores it on exit.
    return render()
  } catch (error) {
    stdin.setRawMode(wasRaw)
    throw error
  }
}
