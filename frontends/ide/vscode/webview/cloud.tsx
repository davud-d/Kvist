import type { ComponentChildren } from "preact"
import { useEffect, useRef, useState } from "preact/hooks"

import { beatFrames, BEATS_TOTAL_MS } from "./beats"
import { onMessage, post } from "./host"

// The panel's two halves, as a switch above them: Kvist Cloud (the cloud's phone page, /m/, framed) and the local
// agent. The cloud page knows it is framed (cloud/mobile/js/native.js) and asks this panel for what it cannot do in
// a frame: signing in (GitHub's page refuses frames; the host signs in in the system browser), opening a link,
// copying. The local half stays mounted while the cloud is shown, so a running turn goes on.

type Surface = "cloud" | "local"

const russianUi = /^ru/i.test(navigator.language)
const LABELS: Record<Surface, string> = { cloud: russianUi ? "Облако" : "Cloud", local: russianUi ? "Локально" : "Local" }
// How far a surface slides as it comes and goes; the rest of the change is its opacity.
const SLIDE_PX = 18

export const cloudOrigin = originOf(document.getElementById("root")?.dataset.cloud ?? "")

function originOf(url: string): string {
  try {
    const parsed = new URL(url)
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.origin : ""
  } catch {
    return ""
  }
}

interface FrameRequest {
  id: string
  name: string
  args?: { url?: string; text?: string }
}

export function Surfaces({ children }: { children: ComponentChildren }) {
  const [surface, setSurface] = useState<Surface>("local")
  // The cloud page loads the first time it is shown, not with the panel.
  const [cloudOpened, setCloudOpened] = useState(false)
  const [signIn, setSignIn] = useState<{ waiting: boolean; error: string }>({ waiting: false, error: "" })
  const frame = useRef<HTMLIFrameElement>(null)
  const thumb = useRef<HTMLSpanElement>(null)
  const cloudPane = useRef<HTMLDivElement>(null)
  const localPane = useRef<HTMLDivElement>(null)
  const running = useRef<Animation[]>([])

  useEffect(() => {
    cloudPane.current?.toggleAttribute("inert", true)
  }, [])

  useEffect(() => onMessage((message) => {
    if (message.type !== "cloudSignIn") return
    setSignIn({ waiting: false, error: message.error ?? "" })
    if (message.url && frame.current) frame.current.src = message.url
  }), [])

  useEffect(() => {
    function onFrameMessage(event: MessageEvent): void {
      const request = (event.data as { kvistCloud?: FrameRequest } | null)?.kvistCloud
      const target = frame.current?.contentWindow
      if (!request || !target || event.source !== target || event.origin !== cloudOrigin) return
      void answer(request).then((result) => target.postMessage({ kvistCloudReply: { id: request.id, result } }, cloudOrigin))
    }
    window.addEventListener("message", onFrameMessage)
    return () => window.removeEventListener("message", onFrameMessage)
  }, [])

  async function answer(request: FrameRequest): Promise<unknown> {
    switch (request.name) {
      case "signIn":
        setSignIn({ waiting: true, error: "" })
        post({ type: "cloudSignIn" })
        return { started: true }
      case "open":
        if (/^https?:\/\//.test(request.args?.url ?? "")) post({ type: "openLink", url: request.args?.url ?? "" })
        return null
      case "copy":
        await navigator.clipboard.writeText(request.args?.text ?? "").catch(() => undefined)
        return null
      default:
        // Wallets, payment sheets and haptics belong to the phone apps.
        return null
    }
  }

  function switchTo(to: Surface): void {
    if (to === surface) return
    if (to === "cloud") setCloudOpened(true)
    setSurface(to)
    move(to)
  }

  function move(to: Surface): void {
    const incoming = to === "cloud" ? cloudPane.current : localPane.current
    const outgoing = to === "cloud" ? localPane.current : cloudPane.current
    if (!incoming || !outgoing || !thumb.current) return
    for (const animation of running.current) animation.cancel()
    incoming.classList.remove("off")
    incoming.toggleAttribute("inert", false)
    outgoing.toggleAttribute("inert", true)
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      outgoing.classList.add("off")
      return
    }
    // The cloud is the left tab: going to it, everything moves right, and back to the left.
    const direction = to === "cloud" ? 1 : -1
    const thumbAt = (progress: number) => (to === "local" ? progress : 1 - progress) * 100
    const options = { duration: BEATS_TOTAL_MS }
    const leaving = outgoing.animate(beatFrames((progress) => ({ opacity: 1 - progress, transform: `translateX(${direction * SLIDE_PX * progress}px)` })), options)
    running.current = [
      thumb.current.animate(beatFrames((progress) => ({ transform: `translateX(${thumbAt(progress)}%)` })), options),
      incoming.animate(beatFrames((progress) => ({ opacity: progress, transform: `translateX(${-direction * SLIDE_PX * (1 - progress)}px)` })), options),
      leaving,
    ]
    leaving.onfinish = () => outgoing.classList.add("off")
  }

  function onSwitchKey(event: KeyboardEvent): void {
    if (event.key === "ArrowLeft") switchTo("cloud")
    else if (event.key === "ArrowRight") switchTo("local")
  }

  const tab = (name: Surface) => (
    <button type="button" role="tab" aria-selected={surface === name} tabIndex={surface === name ? 0 : -1} onClick={() => switchTo(name)}>
      {LABELS[name]}
    </button>
  )

  return (
    <div class="surfaces">
      <div class="surface-bar">
        <div class="surface-switch" role="tablist" data-on={surface} onKeyDown={onSwitchKey}>
          <span class="surface-thumb" ref={thumb} aria-hidden="true" />
          {tab("cloud")}
          {tab("local")}
        </div>
      </div>
      <div class="surface-stage">
        <div class="surface-pane cloud-pane off" ref={cloudPane}>
          <div class="cloud-waiting">Kvist Cloud</div>
          {cloudOpened ? <iframe ref={frame} class="cloud-frame" src={`${cloudOrigin}/m/`} title="Kvist Cloud" allow="clipboard-write" /> : null}
          {signIn.waiting || signIn.error ? (
            <div class={`cloud-note ${signIn.error ? "error" : ""}`} role="status">
              {signIn.error || (russianUi ? "Заверши вход в браузере, панель откроется сама." : "Finish signing in in the browser; the panel follows by itself.")}
            </div>
          ) : null}
        </div>
        <div class="surface-pane" ref={localPane}>{children}</div>
      </div>
    </div>
  )
}
