# -*- coding: utf-8 -*-
"""Terminal clipboard and attachment labels; no model or GUI dependencies."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse
from urllib.request import url2pathname

from kernel.vision.vision import ENVELOPE_TYPE, IMAGE_SUFFIXES, MAX_IMAGE_BYTES, MAX_IMAGES, image_from_bytes


def path_image(text: str) -> dict | None:
    value = text.strip().strip("\"'")
    if value.startswith("file://"):
        parsed = urlparse(value)
        if parsed.netloc not in ("", "localhost"):
            raise ValueError("Only local image files are supported")
        value = url2pathname(parsed.path)
    path = Path(value).expanduser()
    if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        return None
    with path.open("rb") as handle:
        return image_from_bytes(handle.read(MAX_IMAGE_BYTES + 1), path.name)


def clipboard_image() -> dict:
    if sys.platform == "win32":
        command = ["powershell.exe", "-NoProfile", "-STA", "-Command", "Add-Type -AssemblyName System.Windows.Forms; $i=[Windows.Forms.Clipboard]::GetImage(); if($null -eq $i){exit 1}; $m=New-Object IO.MemoryStream; $i.Save($m,[Drawing.Imaging.ImageFormat]::Png); [Console]::Write([Convert]::ToBase64String($m.ToArray()))"]
    elif sys.platform == "darwin":
        command = ["osascript", "-e", "get the clipboard as «class PNGf»"]
    elif os.getenv("WAYLAND_DISPLAY"):
        command = ["wl-paste", "--no-newline", "--type", "image/png"]
    else:
        command = ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("No clipboard image. Paste a file path or use /image PATH / Нет изображения в буфере; используйте /image ПУТЬ") from error
    data = result.stdout
    if sys.platform == "win32":
        import base64
        data = base64.b64decode(data.strip(), validate=True)
    elif sys.platform == "darwin":
        value = data.decode().strip()
        data = bytes.fromhex(value.removeprefix("«data PNGf").removesuffix("»"))
    return image_from_bytes(data, "clipboard.png")


class ImageLabels:
    def __init__(self) -> None:
        self.images: dict[int, dict] = {}
        self.next = 1

    def add(self, image: dict, text: str = "") -> str:
        retained = {int(match[1]) for match in re.finditer(r"\[Image #(\d+)\]", text)}
        self.images = {key: value for key, value in self.images.items() if key in retained}
        if len(self.images) >= MAX_IMAGES:
            raise ValueError("Up to 8 images / Не больше 8 изображений")
        number = self.next
        self.next += 1
        self.images[number] = image
        return f"[Image #{number}]"

    def pack(self, text: str) -> str:
        selected = []
        for match in re.finditer(r"\[Image #(\d+)\]", text):
            item = self.images.get(int(match[1]))
            if item is not None and item not in selected:
                selected.append(item)
        self.images.clear()
        self.next = 1
        return json.dumps({"type": ENVELOPE_TYPE, "text": text, "images": selected}) if selected else text
