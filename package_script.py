# -*- coding: utf-8 -*-
"""Create a reproducible source archive from the committed Git tree."""
from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ARCHIVE = PROJECT_ROOT / "Kvist.zip"


def create_source_archive(
    project_root: Path = PROJECT_ROOT,
    output_path: Path = DEFAULT_ARCHIVE,
) -> Path:
    """Archive the repository's committed HEAD without reading ignored files."""
    root = project_root.resolve(strict=True)
    output = output_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "archive",
            "--format=zip",
            f"--output={output}",
            "HEAD",
        ],
        check=True,
    )
    return output


def main() -> None:
    archive = create_source_archive()
    print(f"Created {archive}")


if __name__ == "__main__":
    main()
