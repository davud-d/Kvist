"""Read configured SSH aliases without opening connections or evaluating Match exec."""
from pathlib import Path


def ssh_aliases(path: Path | None = None) -> dict:
    import paramiko

    path = path or Path.home() / ".ssh" / "config"
    config = paramiko.SSHConfig()
    try:
        with path.open(encoding="utf-8") as handle:
            config.parse(handle)
    except FileNotFoundError:
        return {"aliases": []}
    except (OSError, ValueError, paramiko.SSHException) as error:
        raise ValueError("Could not read ~/.ssh/config. Check its syntax and permissions.") from error
    # Match the transport's parser. Never call lookup here: a Match exec rule
    # may run a local command. Wildcards describe defaults, not selectable hosts.
    # Paramiko get_hostnames() indexes every block's "host" and fails on
    # Match blocks in the installed version. Read only parsed Host blocks.
    names = {name for block in config._config for name in block.get("host", [])}
    aliases = sorted(name for name in names
                     if name and not any(char in name for char in "*?![]"))
    return {"aliases": aliases}
