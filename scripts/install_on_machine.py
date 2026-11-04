# -*- coding: utf-8 -*-
"""Copy this checkout to a saved machine and make its kvist-core run it (see kernel/ssh/deploy.py).

    python scripts/install_on_machine.py NAME [--folder '$HOME/kvist-src']
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.ssh.deploy import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
