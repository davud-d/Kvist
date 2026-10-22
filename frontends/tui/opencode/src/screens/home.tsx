// OpenCode's home screen (packages/tui/src/routes/home.tsx): the wordmark, the prompt and the
// footer. Its update notice, forms and plugin slots have nothing behind them in Kvist.

import { useTerminalDimensions } from "@opentui/solid"
import { createSignal, type JSX } from "solid-js"

import { Logo } from "../oc/tui/component/logo"
import type { KeymapCommand } from "../oc/tui/context/keymap"
import { Prompt, type PromptRef } from "./prompt"
import { HomeFooter, PromptFooter } from "./footers"

const placeholder = ["Fix a TODO in the codebase", "What is the tech stack of this project?", "Fix broken tests"]

export function Home(props: {
  ref?: (ref: PromptRef | undefined) => void
  disabled?: boolean
  commands: readonly KeymapCommand[]
  // Replaces the prompt while an approval waits.
  approval?: JSX.Element
}) {
  const dimensions = useTerminalDimensions()
  const [logoWidth, setLogoWidth] = createSignal(0)
  void logoWidth

  return (
    <>
      <box
        flexGrow={1}
        alignItems="center"
        paddingLeft={dimensions().width < 44 ? 1 : 2}
        paddingRight={dimensions().width < 44 ? 1 : 2}
      >
        <box flexGrow={1} minHeight={0} />
        <box height={3} minHeight={0} flexShrink={1} />
        <box
          flexShrink={0}
          onSizeChange={function () {
            setLogoWidth(this.width)
          }}
        >
          <Logo />
        </box>
        <box height={1} flexShrink={0} />
        <box width="100%" maxWidth={75} zIndex={1000} paddingTop={1} flexShrink={0} position="relative">
          {props.approval ?? (
            <Prompt ref={props.ref} commands={props.commands} placeholders={placeholder} disabled={props.disabled} starters footer={<PromptFooter armed={false} />} />
          )}
        </box>
        <box flexGrow={1} minHeight={0} />
      </box>
      <box width="100%" flexShrink={0}>
        <HomeFooter />
      </box>
    </>
  )
}
