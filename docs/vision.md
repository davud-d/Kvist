# Images and Vision

Kvist accepts images in Electron, Kvist Web, the VS Code / JetBrains chat panel,
and both terminal interfaces. Zed/ACP is unchanged.

- GUI and IDE: use the image attachment button, paste an image, or drop image
  files onto the composer. Hover a thumbnail to remove it; keyboard focus and
  touch also expose the remove button. Sending only images is allowed.
- Terminal: **Ctrl+V** reads a desktop clipboard image. Pasting or dropping a
  local image path inserts `[Image #1]`. Alternatively enter `/image "PATH"`,
  then write your request and send. Remove the marker to omit the image.
  Wayland requires `wl-paste`; X11 requires `xclip`. macOS uses `osascript`,
  Windows uses PowerShell's clipboard API. Remote terminals without desktop
  clipboard access can attach a file present on that machine.
- Classic (Glass): **Ctrl+V** attaches a clipboard image (PNG), or pastes the
  clipboard's text when there is no image. A pasted or dropped path to a PNG or
  JPEG file attaches it too. The input card grows upward to show a thumbnail per
  image; click one to open it at full size, click again or press Esc to close.
  Deleting its `[Image #N]` marker (one Backspace) detaches it.

The independent `kernel/vision/vision.py` service validates and describes images before
passing their descriptions to the text agent. `read_file` and
`read_multiple_files` use the same service for image files, with the executor's
normal workspace and secret-path checks. Descriptions are untrusted tool content.
The original pixels are not retained in session history: later turns and resume
use the saved description. Reattach an image for a new pixel-level inspection.
A crash while analysis is still pending preserves a request to reattach it.

The default Vision model is the current provider's **smart** model. Set
`AGENT_VISION_MODEL` to a different image-understanding model ID on the **same
provider**. No credentials are borrowed from another provider. Python callers
can also set `AgentConfig.vision_model`. Unsupported text-only models produce an
explicit message; unknown/new model IDs are tried using the provider's API, and
API failures are reported without inventing image contents. FreeFlow's current
text-only wrapper cannot carry images.

OpenAI-compatible providers and gateways receive `image_url` content blocks.
The Anthropic adapter translates them to native `image` / base64 blocks.
DeepSeek documents image input for `deepseek-flash`; older `deepseek-chat` and
`deepseek-reasoner` are not assumed to support it. `gpt-image-2` is an image
**generation** model, not a chat model for returning descriptions, so it must
not be selected for this service.

Limits: 8 images, 5 MiB each, 20 MiB total, 25 megapixels per source image.
PNG, JPEG, WebP, GIF, BMP and TIFF are accepted; only the first frame is used.
The core converts images to JPEG (at most 2048 pixels on the longest side),
removes metadata and bounds the provider payload. Very small text may require
cropping the source before attaching it. Vision is a separate, billable call;
provider-reported token usage is emitted as `kind="vision"`.

## Frontend contract

Python frontends may call:

```python
agent.run("Explain this screenshot", images=[{
    "name": "screen.png",
    "data_url": "data:image/png;base64,...",
}])
```

Existing string-goal transports carry the same payload without a protocol
version change:

```json
{"type":"kvist-image-goal","text":"Explain this screenshot","images":[{"name":"screen.png","data_url":"data:image/png;base64,..."}]}
```

Send this JSON as the goal string (or inside the existing `goal.text` field).
Never place it in shell arguments, transcript UI or input-history files. Local
file reads belong to the frontend; the core accepts bytes, not arbitrary paths
from an untrusted remote browser. Remote image URLs are intentionally excluded.

References:
[OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision),
[Anthropic Vision](https://platform.claude.com/docs/en/build-with-claude/vision),
[DeepSeek Vision](https://api-docs.deepseek.com/guides/vision/),
[AnyModel image endpoints](https://anymodel.org/en/blog/embeddings-images-and-audio-through-one-endpoint),
[GPT-Image-2 model card](https://developers.openai.com/api/docs/models/gpt-image-2).
