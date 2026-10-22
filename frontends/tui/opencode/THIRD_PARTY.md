# Third-party material

## OpenCode (MIT)

Everything under `src/oc/` is OpenCode's own source (https://github.com/anomalyco/opencode,
`packages/tui`, `packages/theme`, `packages/plugin`), copied with the same relative paths and
edited only to cut what Kvist has no counterpart for (its server and SDK, plugins, workspaces,
sync) or to replace that with Kvist data. The copy covers the theme system and its 33 colour
themes, the key-binding layer, dialogs, toast, clipboard and selection, the spinner and the logo
components, the diff view, the prompt's metadata row and the transcript's message parts. The
wordmark glyphs in `src/oc/tui/logo.ts` are Kvist's, drawn in OpenCode's letterforms;
`src/oc/tui/config/index.tsx` and `src/oc/client.ts` are small stand-ins for OpenCode's
configuration and SDK types. The screens in `src/screens/` and the files in `src/` outside
`oc/` are written for Kvist on top of that code and follow OpenCode's layout.

```text
MIT License

Copyright (c) 2025 opencode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## OpenTUI (MIT)

`@opentui/core` and `@opentui/solid` (https://github.com/anomalyco/opentui) are dependencies,
installed by `bun install`; they are not copied here.
