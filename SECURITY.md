# Security policy

[Русская версия](SECURITY.ru.md)

## Reporting a vulnerability

Please report security problems privately, not in a public issue. Use GitHub
Security Advisories:

<https://github.com/davud-d/Kvist/security/advisories/new>

Include what you found, the version (`kvist --version`), your platform, and the
steps to reproduce it. If the problem lets a command or the model get past the
sandbox, the approval step or the secret-file rules, say which of them.

Kvist is in beta (0.1.0b1) and only the newest release gets fixes.

## What counts

Kvist is a supervised tool, not a security boundary (see the
[README](README.md#kvist)). These are in scope:

- a way around the executor's approval step, permission rules or sandbox policy;
- the agent reading files it is meant to hide (`.env`, SSH keys, certificates);
- a secret (API key, token) reaching a log, a session file or a request to a
  service other than the one it belongs to;
- an unintended outgoing request, compared with the table in
  [What leaves your machine](README.md#what-leaves-your-machine).

On Windows and on Linux without Bubblewrap there is no network boundary for
commands; Kvist says so at start and in every approval, so that is documented
behaviour and not a vulnerability.
