import { useState } from "preact/hooks"

import type { ProviderInfo } from "../src/messages"

// A short, friendly line per provider for config_info.py's curated "built-in six". Everything else (the ~35 other manual-tier
// vendors, custom endpoints) is reachable later via Settings.
const BLURBS: Record<string, string> = {
  openai: "GPT-5.6 and friends",
  chatgpt: "Use your ChatGPT Plus or Pro plan",
  anthropic: "Claude, direct from Anthropic",
  deepseek: "DeepSeek V4, strong and inexpensive",
  openrouter: "One key, many models",
  omniroute: "Your own local gateway",
  freeflow: "Free Gemini and Groq models",
}

export interface ModelListState {
  provider: string
  busy: boolean
  options: string[]
  error: string
}

interface Props {
  logoUrl: string
  providers: ProviderInfo[]
  busy: boolean
  error: string
  submit: (provider: string, apiKey: string, model: string) => void
  modelList: ModelListState
  onListModels: (provider: string, apiKey: string) => void
}

export function FirstRun({ logoUrl, providers, busy, error, submit, modelList, onListModels }: Props) {
  const [providerId, setProviderId] = useState(providers[0]?.id ?? "")
  const [apiKey, setApiKey] = useState("")
  const [model, setModel] = useState("")
  const provider = providers.find((item) => item.id === providerId)
  // A router/gateway provider has no default model list; the id typed or picked here is
  // used for both roles (fast/smart), tuneable per role later from Settings.
  const needsModel = provider?.requires_manual_models === true
  const oauth = provider?.auth_type === "oauth"
  const models = modelList.provider === providerId ? modelList : null

  function onSubmit(event: Event): void {
    event.preventDefault()
    if (!providerId || (!oauth && !apiKey.trim()) || (needsModel && !model.trim()) || busy) return
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
        {oauth ? (
          <div class="row-hint">Sign in in your browser to use your ChatGPT plan in Kvist. Manage limits at chatgpt.com/settings/usage.</div>
        ) : <input
          class="field"
          type="password"
          autofocus
          disabled={busy}
          placeholder={provider ? `${provider.label} API key` : "API key"}
          value={apiKey}
          onInput={(event) => setApiKey((event.target as HTMLInputElement).value)}
        />}
        {needsModel ? (
          <>
            <div class="first-run-model-row">
              <input
                class="field"
                list="first-run-model-options"
                disabled={busy}
                placeholder="Model id, e.g. kr/claude-sonnet-4.5"
                value={model}
                onInput={(event) => setModel((event.target as HTMLInputElement).value)}
              />
              <button
                type="button"
                class="btn first-run-load-models"
                disabled={busy || models?.busy || !apiKey.trim()}
                onClick={() => onListModels(providerId, apiKey.trim())}
              >
                {models?.busy ? "Loading…" : "Load models"}
              </button>
            </div>
            <datalist id="first-run-model-options">
              {(models?.options ?? []).map((id) => <option key={id} value={id} />)}
            </datalist>
            {models && !models.busy && models.options.length > 0 ? (
              <div class="row-hint first-run-model-hint">{models.options.length} models loaded — start typing to filter.</div>
            ) : null}
            {models && !models.busy && models.options.length === 0 && models.error ? (
              <div class="row-hint first-run-model-hint">Could not load the catalog — enter the model id by hand.</div>
            ) : null}
          </>
        ) : null}
        {error ? <div class="failure">{error}</div> : null}
        <button class="btn primary first-run-submit" type="submit" disabled={busy || (!oauth && !apiKey.trim()) || (needsModel && !model.trim())}>
          {busy ? "Connecting…" : oauth ? "Continue with ChatGPT" : "Continue"}
        </button>
      </form>
    </div>
  )
}
