import { useStdout } from "ink"
import { useCallback, useSyncExternalStore } from "react"

interface WindowSize {
  columns: number
  rows: number
}

interface Store {
  size: WindowSize
  listeners: Set<() => void>
  onResize: () => void
}

const stores = new WeakMap<NodeJS.WriteStream, Store>()

// The previous object is kept while the numbers are equal: `useSyncExternalStore`
// re-renders whenever the snapshot's identity changes.
function measure(stdout: NodeJS.WriteStream, previous?: WindowSize): WindowSize {
  const columns = stdout.columns || 80
  const rows = stdout.rows || 24
  return previous && previous.columns === columns && previous.rows === rows ? previous : { columns, rows }
}

// One `resize` listener per stdout, however many components read the size: Ink's own
// `useWindowSize` adds one per component, and a long transcript of tool cards passed
// Node's limit of 10 (MaxListenersExceededWarning printed over the screen).
function storeFor(stdout: NodeJS.WriteStream): Store {
  const existing = stores.get(stdout)
  if (existing) return existing
  const store: Store = {
    size: measure(stdout),
    listeners: new Set(),
    onResize: () => {
      store.size = measure(stdout, store.size)
      for (const notify of store.listeners) notify()
    },
  }
  stores.set(stdout, store)
  return store
}

function subscribe(stdout: NodeJS.WriteStream, store: Store, listener: () => void): () => void {
  if (store.listeners.size === 0) {
    store.size = measure(stdout, store.size)
    stdout.on("resize", store.onResize)
  }
  store.listeners.add(listener)
  return () => {
    store.listeners.delete(listener)
    if (store.listeners.size === 0) stdout.off("resize", store.onResize)
  }
}

/** The terminal's size in cells; re-renders on resize. Same result as Ink's `useWindowSize`. */
export function useWindowSize(): WindowSize {
  const { stdout } = useStdout()
  const store = storeFor(stdout)
  const onSubscribe = useCallback((listener: () => void) => subscribe(stdout, store, listener), [stdout, store])
  return useSyncExternalStore(onSubscribe, () => store.size)
}
