// Kvist: only the keymap types of OpenCode's plugin context (packages/plugin/src/tui/context.ts), which is
// what the copied keymap context needs; the rest of that file describes the plugin API Kvist does not have.
import type { KeyEvent, Renderable } from "@opentui/core"

export interface KeymapCommand {
  /** Stable command and config keybind identifier. Omit for an inline command. */
  readonly id?: string
  /** Optional label used by command discovery and keyboard-help UI. */
  readonly title?: string
  /** Optional longer description. */
  readonly description?: string
  /** Groups the command in discovery and keyboard-help UI. */
  readonly group?: string
  /** Enables or disables the command. */
  readonly enabled?: boolean | (() => boolean)
  /** Configures automatic binding, or disables it for a named command. */
  readonly bind?: false | string
  /** Adds a named command to the command palette. */
  readonly palette?: true
  /** Adds a named command to prompt slash completion. */
  readonly slash?: {
    readonly name: string
    readonly aliases?: string[]
    /** Keeps the slash command in the prompt and passes its raw input to run. */
    readonly arguments?: true
  }
  /** Promotes the command in discovery UI. */
  readonly suggested?: boolean | (() => boolean)
  /** Executes the command. Keyboard dispatch includes its event; programmatic dispatch does not. Return false to continue. */
  readonly run: (input?: string, event?: KeyEvent) => void | false | Promise<void>
}

export interface KeymapLayer {
  /** Limits the layer to one OpenCode input mode. Use global to opt out; defaults to base. */
  readonly mode?: string
  /** Enables or disables the complete layer. */
  readonly enabled?: boolean | (() => boolean)
  /** Limits the layer to a focused renderable. */
  readonly target?: () => Renderable | null | undefined
  /** Resolves conflicts with other active layers. */
  readonly priority?: number
  /** Commands owned by this layer. */
  readonly commands?: readonly KeymapCommand[]
  /** IDs of commands whose configured bindings should be active in this layer. */
  readonly bindings?: readonly string[]
}

export interface KeymapPending {
  readonly key: string
  readonly token?: string
}

export interface KeymapActive {
  readonly key: string
  readonly title?: string
  readonly description?: string
  readonly group?: string
  readonly continues: boolean
}

export interface Keymap {
  /**
   * Creates a reactive keymap layer. It is removed when the plugin deactivates, or earlier when the component or reactive
   * computation that created it is disposed. The factory must be pure. It runs once untracked for initial command-shape
   * validation, then reactively. Initial command-shape errors throw synchronously. OpenTUI handles binding-parse errors.
   * Errors from later reactive updates follow Solid's error handling.
   */
  layer(factory: () => KeymapLayer): void
  /** Dispatches a reachable command by ID. */
  dispatch(id: string, input?: string): void
  /** Returns every formatted shortcut for a registered command. */
  shortcuts(id: string): readonly string[]
  /** Returns the currently reachable commands. Reactive when read in a Solid computation. */
  commands(): readonly KeymapCommand[]
  /** Returns the pending key sequence. Reactive when read in a Solid computation. */
  pending(): readonly KeymapPending[]
  /** Returns bindings reachable from the pending key sequence. Reactive when read in a Solid computation. */
  active(): readonly KeymapActive[]
  /** Controls mutually exclusive OpenCode input modes. */
  readonly mode: {
    /** Returns the active mode. */
    current(): string
    /** Pushes a mode until the returned cleanup is called. */
    push(mode: string): () => void
  }
}

