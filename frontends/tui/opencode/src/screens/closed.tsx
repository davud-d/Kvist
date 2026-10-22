// What stands in the prompt's place when end_session closed the session, laid out as Kvist Glass does it:
// one row in the prompt's frame, the core's notice on the left, and on the right the key and a button that
// does the same thing. A calm stop, not an error: muted text, no editor, so nothing typed or pasted can
// reach the closed session.

import { useKeyboard } from "@opentui/solid"
import { createMemo } from "solid-js"

import { useTheme } from "../oc/tui/context/theme"
import { SplitBorder } from "../oc/tui/ui/border"

// The core's notice ends with "· Enter — new session"; the button says that, so the words are not repeated.
export function noticeWords(notice: string): string {
  const parts = notice.split(" · ")
  return parts.length > 1 && /enter/i.test(parts.at(-1)!) ? parts.slice(0, -1).join(" · ") : notice
}

// The button speaks the language of the notice (the core writes it in the language of the conversation).
export function buttonLabel(notice: string): string {
  return /[а-яё]/i.test(notice) ? "Новая сессия" : "New session"
}

export function ClosedBar(props: { notice: string; active: boolean; onNewSession(): void }) {
  const theme = useTheme()
  const background = createMemo(() => theme.decrease(theme.background.raised.base))
  useKeyboard((event) => {
    if (!props.active || event.name !== "return") return
    event.preventDefault()
    props.onNewSession()
  })
  return (
    <box
      border={["left"]}
      borderColor={theme.border.base}
      customBorderChars={SplitBorder.customBorderChars}
      backgroundColor={background()}
      paddingLeft={2}
      paddingRight={2}
      paddingTop={1}
      paddingBottom={1}
      flexShrink={0}
      flexDirection="row"
      alignItems="center"
      gap={2}
    >
      <text fg={theme.text.muted} flexShrink={0} selectable={false}>
        ×
      </text>
      <text fg={theme.text.muted} flexGrow={1} flexShrink={1} wrapMode="none" truncate>
        {noticeWords(props.notice)}
      </text>
      <text fg={theme.text.muted} flexShrink={0} selectable={false}>
        enter
      </text>
      <box
        flexShrink={0}
        paddingLeft={1}
        paddingRight={1}
        backgroundColor={theme.background.action.primary.focused}
        onMouseUp={() => props.active && props.onNewSession()}
      >
        <text fg={theme.text.action.primary.focused} attributes={1} selectable={false}>
          {buttonLabel(props.notice)}
        </text>
      </box>
    </box>
  )
}
