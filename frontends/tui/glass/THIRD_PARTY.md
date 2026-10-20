# Third-party notices

Kvist Classic's binary includes the following.

## RaTeX (math typesetting and drawing)

Crates `ratex-parser`, `ratex-layout`, `ratex-render`, `ratex-types`, `ratex-font`,
`ratex-font-loader`, `ratex-lexer`, `ratex-katex-fonts`: https://github.com/erweixin/RaTeX

```
MIT License

Copyright (c) erweixin

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

## KaTeX fonts

The `KaTeX_*.ttf` fonts embedded by `ratex-katex-fonts`, from https://github.com/KaTeX/KaTeX.
They are not under the MIT License above: they are licensed under the SIL Open Font
License, Version 1.1, used here unmodified. The licence text is in
[licenses/OFL-KaTeX-fonts.txt](licenses/OFL-KaTeX-fonts.txt); the fonts' own copyright
statements are in the RaTeX repository's `THIRD_PARTY_NOTICES.txt` and `licenses/`.

The other dependencies and their licences: `cargo tree --edges normal --format "{p} {l}"`.
