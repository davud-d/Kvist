# Providers, models, effort, context, environment

## Providers

`/provider` selects and configures one. Settings live in `~/.kvist/config.json`
(`KVIST_CONFIG_DIR` moves the folder).

- **AnyModel**: the default on a blank config, with its own registration flow.
- Core providers: OpenAI, Anthropic, DeepSeek, OpenRouter, OmniRoute (a local
  OpenAI-compatible gateway, default `http://localhost:20128/v1`), FreeFlow (beta).
- **ChatGPT Plus/Pro** by browser sign-in, no API key; tokens are stored in the config
  (mode 0600); `/change-api` reauthorizes; usage counts against the person's plan.
- About 36 other OpenAI-compatible vendors (Qwen, Moonshot, Mistral, Groq, xAI, Together,
  Cohere, Gemini and others): one API key and an optional regional base URL each.
- A custom endpoint of the person's own, OpenAI- or Anthropic-compatible (`custom:` prefix).

`AGENT_LLM_PROVIDER` overrides the saved provider for one launch.

## Fast and smart

Each provider has a **fast** model for routine steps and a **smart** model. A run starts on
fast and moves to smart, once and never back, on parse errors, repeated failures or
stagnation, unless the person pinned a model. Choose both with `/models`; the picker
filters by typing. The model list is loaded from the provider at launch; switch that off
under `/settings-api` if discovery is slow.

## Reasoning effort

`/effort` sets `false | low | medium | high | xhigh | max` (default `medium`), translated
per provider. `false` turns thinking off where the provider allows it. If a provider
rejects the setting, Kvist retries without it and warns. `AGENT_REASONING_EFFORT` wins for one launch.

## Context

The prompt budget is 85% of the model's maximum **input** (not its context window), so
a 1M-input model gets 850k. A model newer than Kvist's table inherits its family's limit;
if a provider rejects a prompt as too long, Kvist lowers the budget for that model and
resends. `/context` shows usage. `AGENT_MAX_CONTEXT_TOKENS` or a context override in
`/settings-api` pins a value (a limit learned from a rejection still wins).

## Native tools or JSON

On the first task of a launch Kvist tests once whether the model calls tools natively. If
not, it uses a plain JSON protocol, which is why weak and free models work. `AGENT_NATIVE_TOOLS=0`
forces JSON.

## Environment variables

| Variable | Effect |
| --- | --- |
| `AGENT_LLM_PROVIDER`, `AGENT_FAST_MODEL`, `AGENT_SMART_MODEL` | Provider and models for one launch |
| `AGENT_REASONING_EFFORT` | Effort for one launch |
| `AGENT_MAX_STEPS` | Steps before Kvist asks whether to continue (default 150) |
| `AGENT_MAX_CONTEXT_TOKENS` | Pin the prompt budget |
| `AGENT_API_INTERVAL` | Minimum gap between requests |
| `AGENT_NATIVE_TOOLS` | `0` forces the JSON protocol |
| `AGENT_SANDBOX_MODE`, `AGENT_SANDBOX_NET` | Sandbox type (`auto`, `workspace`, `bubblewrap`) and network (`allow`, `deny`) |
| `AGENT_WRITE_APPROVAL` | `0` turns off the overwrite approval |
| `AGENT_TOOL_PATHS` | Extra toolchain directories (absolute, must exist, not world-writable) |
| `AGENT_VISION_MODEL` | Image-understanding model on the same provider |
| `AGENT_VERBOSE`, `AGENT_PERSIST_LOGS` | Diagnostics and run logs |
| `KVIST_CONFIG_DIR` | Where `config.json` lives |
| `SEARCH_PROVIDER`, `SEARXNG_BASE_URL`, `TAVILY_API_KEY`, `BRAVE_SEARCH_API_KEY` | Web search |

Prefer the interactive commands. Never print a key from the config; read only the
non-secret fields you need.
