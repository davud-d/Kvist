import { useState } from "preact/hooks"

import type { ProviderInfo } from "../src/messages"

// A short, friendly line per provider — AnyModel (Kvist's own shipped default) plus
// config_info.py's curated "built-in six". Everything else (the ~35 other manual-tier
// vendors, custom endpoints) is reachable later via Settings.
const BLURBS: Record<string, string> = {
  anymodel: "Kvist's own default router",
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
  submit: (provider: string, apiKey: string, model: string) => void
}

export function FirstRun({ logoUrl, providers, busy, error, submit }: Props) {
  const [providerId, setProviderId] = useState(providers[0]?.id ?? "")
  const [apiKey, setApiKey] = useState("")
  const [model, setModel] = useState("")
  const provider = providers.find((item) => item.id === providerId)
  // A router/gateway provider has no default model list; the id typed here is used
  // for all three roles (fast/smart/planner), tuneable per role later from Settings.
  const needsModel = provider?.requires_manual_models === true

  function onSubmit(event: Event): void {
    event.preventDefault()
    if (!providerId || !apiKey.trim() || (needsModel && !model.trim()) || busy) return
    submit(providerId, apiKey.trim(), model.trim())
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
        {needsModel ? (
          <input
            class="field"
            disabled={busy}
            placeholder="Model id, e.g. kr/claude-sonnet-4.5"
            value={model}
            onInput={(event) => setModel((event.target as HTMLInputElement).value)}
          />
        ) : null}
        {error ? <div class="failure">{error}</div> : null}
        <button class="btn primary first-run-submit" type="submit" disabled={busy || !apiKey.trim() || (needsModel && !model.trim())}>
          {busy ? "Connecting…" : "Continue"}
        </button>
      </form>
    </div>
  )
}
