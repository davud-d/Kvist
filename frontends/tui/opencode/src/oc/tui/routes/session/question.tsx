// Kvist: OpenCode's SessionQuestion (packages/tui/src/routes/session/permission.tsx) as it is, minus the
// simulation hooks. It draws a question in the prompt's place with a row of answers under it.
import { createStore } from "solid-js/store"
import { createMemo, For, Show } from "solid-js"
import { Portal, useRenderer, useTerminalDimensions, type JSX } from "@opentui/solid"
import { useTheme } from "../../context/theme"
import { SplitBorder } from "../../ui/border"
import { Keymap } from "../../context/keymap"

export function SessionQuestion<const T extends Record<string, string>>(props: {
  title: string
  semanticLabel?: string
  instance: string
  id?: string
  group?: string
  choicesLabel?: string
  header?: JSX.Element
  body: JSX.Element | ((option: keyof T) => JSX.Element)
  options: T
  escapeKey?: keyof T
  fullscreen?: boolean
  onSelect: (option: keyof T) => void
}) {
  const theme = useTheme()
  const dimensions = useTerminalDimensions()
  const keys = Object.keys(props.options) as (keyof T)[]
  const [store, setStore] = createStore({
    selected: keys[0],
    expanded: false,
  })
  const narrow = createMemo(() => dimensions().width < 80)
  const shortcuts = Keymap.useShortcuts()
  const id = () => props.id ?? "session.permission"
  const group = () => props.group ?? "Permission"
  const dismiss = () => {
    if (store.expanded) {
      setStore("expanded", false)
      return
    }
    if (props.escapeKey) props.onSelect(props.escapeKey)
  }

  Keymap.createLayer(() => ({
    mode: "base",
    commands: [
      ...(props.escapeKey
        ? [
            {
              id: "app.exit",
              title: "Reject permission",
              group: group(),
              bind: false as const,
              run: dismiss,
            },
          ]
        : []),
      ...(props.fullscreen
        ? [
            {
              id: "permission.prompt.fullscreen",
              title: "Toggle permission fullscreen",
              group: group(),
              bind: false as const,
              run: () => setStore("expanded", (value) => !value),
            },
          ]
        : []),
      ...(keys.length > 1
        ? [
            {
              bind: "left,h",
              title: "Previous option",
              group: group(),
              run: () => {
                const index = keys.indexOf(store.selected)
                setStore("selected", keys[(index - 1 + keys.length) % keys.length])
              },
            },
            {
              bind: "right,l",
              title: "Next option",
              group: group(),
              run: () => {
                const index = keys.indexOf(store.selected)
                setStore("selected", keys[(index + 1) % keys.length])
              },
            },
          ]
        : []),
      {
        bind: "return",
        title: "Select option",
        group: group(),
        run: () => props.onSelect(store.selected),
      },
      ...(props.escapeKey ? [{ bind: "escape", title: "Reject permission", group: group(), run: dismiss }] : []),
    ],
    bindings: [...(props.escapeKey ? ["app.exit"] : []), ...(props.fullscreen ? ["permission.prompt.fullscreen"] : [])],
  }))

  const hint = createMemo(() => (store.expanded ? "minimize" : "fullscreen"))
  useRenderer()

  const content = () => (
    <box
      id={id()}
      backgroundColor={theme.background.raised.base}
      border={["left"]}
      borderColor={theme.background.action.primary.focused}
      customBorderChars={SplitBorder.customBorderChars}
      {...(store.expanded
        ? { top: dimensions().height * -1 + 1, bottom: 1, left: 2, right: 2, position: "absolute" }
        : {
            top: 0,
            maxHeight: 15,
            bottom: 0,
            left: 0,
            right: 0,
            position: "relative",
          })}
    >
      <box gap={1} paddingLeft={1} paddingRight={3} paddingTop={1} paddingBottom={1} flexGrow={1}>
        <Show
          when={props.header}
          fallback={
            <box flexDirection="row" gap={1} paddingLeft={1} flexShrink={0}>
              <text fg={theme.text.feedback.warning.base}>{"△"}</text>
              <text fg={theme.text.base}>{props.title}</text>
            </box>
          }
        >
          <box paddingLeft={1} flexShrink={0}>
            {props.header}
          </box>
        </Show>
        {typeof props.body === "function" ? props.body(store.selected) : props.body}
      </box>
      <box
        flexDirection={narrow() ? "column" : "row"}
        flexShrink={0}
        gap={1}
        paddingTop={1}
        paddingLeft={2}
        paddingRight={3}
        paddingBottom={1}
        backgroundColor={theme.decrease(theme.background.raised.base)}
        justifyContent={narrow() ? "flex-start" : "space-between"}
        alignItems={narrow() ? "flex-start" : "center"}
      >
        <box
          id={`${id()}.actions`}
          flexDirection="row"
          gap={1}
          flexShrink={0}
        >
          <For each={keys}>
            {(option) => (
              <box
                id={`${id()}.action.${String(option)}`}
                paddingLeft={1}
                paddingRight={1}
                backgroundColor={
                  option === store.selected
                    ? theme.background.action.primary.focused
                    : theme.background.action.primary.base
                }
                onMouseMove={() => setStore("selected", option)}
                onMouseUp={() => {
                  setStore("selected", option)
                  props.onSelect(option)
                }}
              >
                <text
                  fg={option === store.selected ? theme.text.action.primary.focused : theme.text.action.primary.base}
                >
                  {props.options[option]}
                </text>
              </box>
            )}
          </For>
        </box>
        <box flexDirection="row" gap={2} flexShrink={0}>
          <Show when={props.fullscreen}>
            <text fg={theme.text.base}>
              {shortcuts.get("permission.prompt.fullscreen")} <span style={{ fg: theme.text.muted }}>{hint()}</span>
            </text>
          </Show>
          <Show when={keys.length > 1}>
            <text fg={theme.text.base}>
              {"⇆"} <span style={{ fg: theme.text.muted }}>select</span>
            </text>
          </Show>
          <text fg={theme.text.base}>
            enter <span style={{ fg: theme.text.muted }}>confirm</span>
          </text>
        </box>
      </box>
    </box>
  )

  return (
    <Show when={!store.expanded} fallback={<Portal>{content()}</Portal>}>
      {content()}
    </Show>
  )
}
