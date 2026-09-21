import type { ToHost } from "../src/messages"

interface VsCodeApi {
  postMessage(message: ToHost): void
}

declare function acquireVsCodeApi(): VsCodeApi

const api = acquireVsCodeApi()

export const post = (message: ToHost): void => api.postMessage(message)
