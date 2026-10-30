# ACP Registry entry (draft)

`kvist/` is the directory to submit to https://github.com/agentclientprotocol/registry as a pull request. Nothing here is submitted yet.

Before the pull request:

1. Publish a GitHub release `v0.1.0` with the five binaries named in `agent.json`. The registry rejects `/latest/` URLs and checks that the version in the URL matches `version`, so the rolling `latest-plugin-build` release does not qualify.
2. Add `sha256` for each archive (`sha256sum kvist-linux-x64`).
3. Check how the registry wants a raw binary written in `cmd`; the `./name` form here is a guess from its format notes.
4. Run the registry's own checks on a fork: `uv run --with jsonschema .github/workflows/build_registry.py` and `python3 .github/workflows/verify_agents.py --auth-check --agent kvist`.

Authentication: `initialize` already returns a terminal auth method (`--acp --login`), which is what the auth check looks for.
