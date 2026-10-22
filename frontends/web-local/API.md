# Kvist Web API v1

The browser is a replaceable client of the local controller. Its stable
boundary is HTTP/JSON plus server-sent events under `/api/v1`; it never imports
Kvist Python modules and never addresses a process, SSH connection, journal, or
configuration file directly.

## Transport and security

- `GET /` creates an HttpOnly, SameSite=Strict browser session and embeds a
  per-session CSRF token in the HTML shell.
- Every mutation sends that token in `X-Kvist-CSRF` and a same-origin cookie.
- Every request is subject to Host validation; mutations are additionally
  subject to exact Origin validation.
- `GET /api/v1/events?after=<cursor>&epoch=<epoch>` is an SSE stream of
  `LiveEnvelope` values. `epoch` is optional only for an older v1 page; new
  clients retain it from bootstrap and send it on every fresh EventSource.
- Errors are JSON `{ "ok": false, "error": "human-readable message" }`.
- Existing `/api/*` v1 paths remain accepted so a page open during an upgrade
  is not broken. New clients must use `/api/v1/*`.

## Identity and cursors

A session is identified everywhere by:

```json
{"server_id":"local","workspace":"/absolute/workspace","session":"chat-name"}
```

Durable history records are:

```json
{"seq":1843,"turn":12,"ts":1760000000.0,"event":{"type":"step"}}
```

`seq` is scoped to one session journal. A reconnecting client requests
`history?after=1842`; live SSE has its own controller-scoped `LiveEnvelope.id`
cursor. `bootstrap.event_stream` contains that cursor plus an opaque `epoch`.
The UI reducer consumes the existing `kernel.protocol.frontend_protocol` event objects
without learning how they were produced.

Live envelopes have this stable shape:

```json
{
  "id": 51,
  "server_id": "local",
  "workspace": "/absolute/workspace",
  "session": "chat-name",
  "event": {"type": "stream_chunk", "stream_id": "...", "text": "..."},
  "ts": 1760000000.0,
  "durable": false
}
```

`durable: true` means the event is also present in the addressed session
journal and must be reconciled by the per-session `seq` cursor, rather than
trusted as a second transcript. `stream_chunk`, `action_output`, `job_output`, status, `command_result`
(the core writes it outside the journal) and connection/process notifications
are transient. A client may paint a `stream_start` eagerly so
chunks have a target, but must make its later durable replay idempotent.

If an SSE cursor belongs to a different controller epoch, lies ahead of its
current stream, or has fallen out of the bounded live buffer, the server sends
the global control envelope:

```json
{"event":{"type":"stream.reset","epoch":"new-epoch","reason":"history_gap"}}
```

It is not a session event. A client refreshes navigation and replays the
selected session's durable history. For a controller reset it also recreates
the EventSource with the supplied cursor/epoch; a forwarded remote reset has
`event.remote: true` and does not replace the controller epoch.

Input requests use the public event protocol: `kind` is one of `confirm`,
`choice`, `text`, or `secret`. `choice` supplies `{value,label}` options;
confirmation subjects are typed (`command` with command/reason, or `overwrite`
with path/diff). Secret request events never contain a current secret value and
the submitted response is never included in an event DTO.

## Read endpoints

- `GET /api/v1/bootstrap` → protocol version, navigation snapshot, live cursor.
- `GET /api/v1/navigation` → servers, connection state, workspaces, sessions,
  execution state, unread state, and unified inbox.
- `GET /api/v1/sessions?server=&workspace=` → one workspace's session metadata.
- `GET /api/v1/history?server=&workspace=&session=&after=&limit=` → durable
  records strictly newer than `after`.
- `GET /api/v1/changes?server=&workspace=&session=` → session diff report.
- `GET /api/v1/config-sync` → non-secret configuration and selectable credential
  metadata. Credential values are never returned.
- `GET /api/v1/configuration?server=` → the core's provider catalog and saved
  defaults: providers with credential presence (never values), `setup_required`,
  `registration_url` (the core's AnyModel registration address), `effort`,
  `effort_levels`, `effort_info` and `roles` (values with the core's descriptions),
  the saved `role`, and `recommended_models` (per provider, in order: the first one
  the live catalog offers is the first run's preselected model).

## Mutation endpoints

- `POST /api/v1/servers` — persist a `ServerSpec`; a submitted password is moved
  into the secret backend and is not part of the returned DTO.
- `POST /api/v1/servers/connect` — connect/bootstrap, or return
  `trust_required` with an SSH fingerprint for explicit confirmation.
- `POST /api/v1/workspaces` — add a workspace to one server runtime.
- `POST /api/v1/sessions` — create a session without starting an agent.
- `POST /api/v1/send` — lazily start/reuse the session agent and submit a goal.
- `POST /api/v1/stop` — cancel only the addressed session's current turn.
- `POST /api/v1/respond` — answer a typed pending input request.
- `POST /api/v1/read` — monotonically advance `last_read_event_id`.
- `POST /api/v1/config-sync` — sync general settings and only the explicitly
  selected credential ids.
- `POST /api/v1/configuration` — check supplied credentials and save provider,
  models, `effort` and an optional `role` through the core's writers.
- `POST /api/v1/configuration/models` — the provider's live model list for saved
  or supplied credentials, saving nothing. A typed AnyModel key is checked first, so
  a refused key and an unreachable AnyModel give different messages.
- `POST /api/v1/command` — `models`, `effort` or `role` for an idle session's
  running core; the answer arrives as a transient `command_result` event.

The backend layers behind this contract are `WebController` → `Runtime`
(`LocalRuntime` or `RemoteRuntime`) → core/bridge. Replacing everything under
`kvist_web/client/` requires no change to those layers.
