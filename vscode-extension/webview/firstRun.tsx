import { useState } from "preact/hooks"

import type { ProviderInfo } from "../src/messages"

// A short, friendly line per provider — the same six config_info.py curates as
// "built-in", each usable with just one pasted key. Everything else (the ~36
// manual-tier vendors, custom endpoints) is reachable later via Settings.
const BLURBS: Record<string, string> = {
  openai: "GPT-5.6 and friends",
  anthropic: "Claude, direct from Anthropic",
  deepseek: "DeepSeek V4, strong and inexpensive",
  openrouter: "One key, many models",
  omniroute: "Your own local gateway",
  freeflow: "Free Gemini and Groq models",
}

interface Props {
  logoUrl: string
  providers: ProviderInfo[]
  busy: boolean
  error: string
  submit: (provider: string, apiKey: string) => void
}

export function FirstRun({ logoUrl, providers, busy, error, submit }: Props) {
  const [providerId, setProviderId] = useState(providers[0]?.id ?? "")
  const [apiKey, setApiKey] = useState("")
  const provider = providers.find((item) => item.id === providerId)

  function onSubmit(event: Event): void {
    event.preventDefault()
    if (!providerId || !apiKey.trim() || busy) return
    submit(providerId, apiKey.trim())
  }

  return (
    <div class="first-run">
      <img class="logo first-run-logo" src={logoUrl} alt="Kvist" />
      <div class="welcome-title">Set up Kvist</div>
      <div class="row-hint first-run-lede">Pick a provider and paste its API key. Add more, or switch, anytime from Settings.</div>

      <div class="first-run-providers" role="radiogroup">
        {providers.map((item) => (
          <button
            key={item.id}
            type="button"
            role="radio"
            aria-checked={item.id === providerId}
            class={`starter first-run-provider ${item.id === providerId ? "on" : ""}`}
            disabled={busy}
            onClick={() => setProviderId(item.id)}
          >
            <span class="first-run-provider-label">{item.label}</span>
            <span class="first-run-provider-blurb">{BLURBS[item.id] ?? ""}</span>
          </button>
        ))}
      </div>

      <form class="first-run-form" onSubmit={onSubmit}>
        <input
          class="field"
          type="password"
          autofocus
          disabled={busy}
          placeholder={provider ? `${provider.label} API key` : "API key"}
          value={apiKey}
          onInput={(event) => setApiKey((event.target as HTMLInputElement).value)}
        />
        {error ? <div class="failure">{error}</div> : null}
        <button class="btn primary first-run-submit" type="submit" disabled={busy || !apiKey.trim()}>
          {busy ? "Connecting…" : "Continue"}
        </button>
      </form>
    </div>
  )
}
