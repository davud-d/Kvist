import type { ToHost, ToWebview } from "../src/messages"

// The panel runs inside two IDEs: a VS Code webview and a JetBrains JCEF browser. This
// file is the only place that knows which; the rest of the UI sees `post` and `onMessage`.

interface VsCodeApi {
  postMessage(message: ToHost): void
}

// Defined by the JetBrains plugin in a script that runs before this bundle: `post` is a
// JBCefJSQuery call, and the plugin delivers its messages by calling `receive`.
interface InjectedHost {
  post(json: string): void
  receive?: (message: ToWebview) => void
}

declare const acquireVsCodeApi: (() => VsCodeApi) | undefined

declare global {
  interface Window {
    kvistHost?: InjectedHost
  }
}

type Listener = (message: ToWebview) => void

const listeners = new Set<Listener>()

function deliver(message: ToWebview): void {
  for (const listener of listeners) listener(message)
}

function connect(): (message: ToHost) => void {
  if (typeof acquireVsCodeApi === "function") {
    const api = acquireVsCodeApi()
    window.addEventListener("message", (event: MessageEvent<ToWebview>) => deliver(event.data))
    return (message) => api.postMessage(message)
  }
  const injected = window.kvistHost
  if (!injected) throw new Error("Kvist: the panel was opened outside VS Code and JetBrains IDEs.")
  injected.receive = deliver
  return (message) => injected.post(JSON.stringify(message))
}

const send = connect()

export const post = (message: ToHost): void => send(message)

export function onMessage(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
