import { useEffect, useRef } from "preact/hooks"

// A soft glow that trails the pointer. It eases toward the target each frame
// and stops the loop once it has arrived, so an idle panel costs nothing.
// The eased position is also published as --px/--py, which lights the borders
// of `.lit` surfaces near the pointer.
export function CursorGlow() {
  const glow = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const element = glow.current
    if (!element || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return
    const root = document.documentElement
    const pos = { x: window.innerWidth / 2, y: window.innerHeight / 2 }
    const target = { ...pos }
    let frame = 0

    const paint = () => {
      element.style.transform = `translate3d(${pos.x}px, ${pos.y}px, 0)`
      root.style.setProperty("--px", `${pos.x}px`)
      root.style.setProperty("--py", `${pos.y}px`)
    }
    const step = () => {
      pos.x += (target.x - pos.x) * 0.16
      pos.y += (target.y - pos.y) * 0.16
      paint()
      const arrived = Math.abs(target.x - pos.x) <= 0.4 && Math.abs(target.y - pos.y) <= 0.4
      frame = arrived ? 0 : requestAnimationFrame(step)
    }
    const onMove = (event: PointerEvent) => {
      target.x = event.clientX
      target.y = event.clientY
      element.classList.add("is-active")
      root.classList.add("pointer")
      if (!frame) frame = requestAnimationFrame(step)
    }
    const hide = () => {
      element.classList.remove("is-active")
      root.classList.remove("pointer")
    }

    paint()
    window.addEventListener("pointermove", onMove)
    document.addEventListener("mouseleave", hide)
    window.addEventListener("blur", hide)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener("pointermove", onMove)
      document.removeEventListener("mouseleave", hide)
      window.removeEventListener("blur", hide)
    }
  }, [])

  return <div ref={glow} class="cursor-glow" aria-hidden="true" />
}
