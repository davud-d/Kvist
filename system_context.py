# -*- coding: utf-8 -*-
"""
This module is needed to improve the AI agent’s accuracy by ensuring that the model is aware of the environment in which it is operating.
For example:
- Operating system
- Operating system version (if Windows)
- Current shell (bash, fish, zsh)
- Current time.
"""

### IMPORT
from datetime import datetime
import os
import platform
import shutil
import sys
import distro
import subprocess


### SYSTEM CONTEXT


def current_time_context():
    """Returns the current time in the format 'YYYY-MM-DD HH:MM:SS Timezone'."""
    return {
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def current_os_context():
    # LINUX
    if platform.system().lower() == "linux":
        # get distribution name
        try:
            return f"{distro.name()} {distro.version()}"
        except:
            return "Linux: distribution name unavailable"

    # MACOS
    elif platform.system().lower() == "darwin":
        try:
            return platform.mac_ver()[0]
        except:
            return "Mac OS: version unavailable"
    
    # WINDOWS
    elif platform.system().lower() == "windows":
        return f"{platform.release()} {platform.version()}"        
    
    # UNKNOWN OS
    else:
        return f"unknown os: {platform.system()}"

def current_shell_context():
    """Returns the name of the current shell environment (e.g. 'bash', 'fish', 'powershell', 'cmd')."""
    current_platform = platform.system().lower()

    # LINUX
    if current_platform == "linux":
        shell = os.environ.get("SHELL", "")
        return os.path.basename(shell) if shell else "unknown"

    # WINDOWS
    elif current_platform == "windows":
        comspec = os.environ.get("COMSPEC", "")
        if comspec:
            name = os.path.splitext(os.path.basename(comspec))[0].lower()
            return "powershell" if "powershell" in name or "pwsh" in name else name
        return "unknown"

    # macOS (experimental, not officially supported)
    elif current_platform == "darwin":
        shell = os.environ.get("SHELL", "")
        return os.path.basename(shell) if shell else "unknown"

    else:
        return "unknown"
