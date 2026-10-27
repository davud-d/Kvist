# Skills, MCP, helpers

## Skills

A skill is an installable package: instructions (Markdown) and, optionally, Python tools that
run in an OS sandbox with only the permissions the person approved. Executable skills need
Linux; instruction-only skills work everywhere. The model sees a short catalog and attaches
a skill when a task needs it (up to 4 per task); a skill with tools asks the person on first use.

Preinstalled: `browser` (Chromium on its own virtual screen), `desktop` (open addresses and apps,
list/focus/close windows), `visualize` (diagrams, charts, 3D pages), `test_runner`, `documents`
(PDF, xlsx, docx), `code_nav` (Python), `project_overview`, `static_checks`, `debugging`,
`design`, `skill_authoring`, `kvist_docs`.

Managing: `kvist --skill-op list|show|install|approve|install-deps|revoke|rollback|uninstall|check`.
`install` never runs the package; `approve` is always the person's dialog; a changed package
starts with no grants. Bring back an uninstalled bundled skill with
`--skill-op install --skill-path bundled:<id>`. To write one, attach `skill_authoring`.
Full format: `docs/SKILLS.md`.

## MCP servers

Only the person configures them: `kvist --mcp-op list|add|remove`. Local stdio servers only. Their tools
appear as `mcp__<server>__<tool>` after approval; a tool counts as read-only only if the person listed it.

## Helpers

The agent can start one helper at a time in its own session (`helper-…`) for a side task. A helper
has a role; it may write unless its role is reader or the main agent may not write. `delegate_task`
is not a helper and starts nobody.

## Other tools the agent has

Web search (`/web-search-provider`), fetching documentation pages, image input (`Ctrl+V`, `/image PATH`),
image generation when the provider is AnyModel, and a throwaway scratchpad directory for test scripts.
