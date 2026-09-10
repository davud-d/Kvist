Kvist landing page webfont
===========================

Source font
-----------
JetBrains Mono, version 2.304 (Arch package ttf-jetbrains-mono 2.304-2).
Source files used (system-installed, static weights only — no variable
font was available on this machine):
  /usr/share/fonts/TTF/JetBrainsMono-Regular.ttf
  /usr/share/fonts/TTF/JetBrainsMono-Bold.ttf

Both source files were verified (via fontTools) to have full Cyrillic
cmap coverage (U+0410-044F plus U+0401/U+0451) before subsetting.

License
-------
SIL Open Font License, Version 1.1. Full text copied to OFL.txt in this
directory (from /usr/share/licenses/ttf-jetbrains-mono/LICENSE).
Copyright 2020 The JetBrains Mono Project Authors
(https://github.com/JetBrains/JetBrainsMono).

Why static instead of variable
-------------------------------
No variable-font build of JetBrains Mono (JetBrainsMono[wght].ttf) was
present on this machine, only the pre-built static weights. Per the task's
fallback instructions, this build uses static Regular (400) + Bold (700)
rather than downloading a VF over the network. Two weights x two script
subsets = 4 files, all comfortably under the 90 KB combined budget.

Subsetting
----------
Tool: fontTools 4.64.0 (pip install fonttools brotli), invoked as
`python -m fontTools.subset` inside the project's .venv.

Latin subset (unicode-range fed to pyftsubset --unicodes, fish-compatible
— the "*" in --layout-features=* must be quoted under fish or it is
treated as a glob):

  .venv/bin/python -m fontTools.subset \
    /usr/share/fonts/TTF/JetBrainsMono-Regular.ttf \
    --output-file=web/assets/fonts/kvist-mono-latin-400.woff2 \
    --flavor=woff2 "--layout-features=*" --no-hinting --desubroutinize \
    --unicodes="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD,U+2500-257F,U+2580-259F,U+25A0-25FF,U+2022,U+00B7,U+2026,U+2190-2199,U+2713,U+2717,U+25CF,U+25CB"

  .venv/bin/python -m fontTools.subset \
    /usr/share/fonts/TTF/JetBrainsMono-Bold.ttf \
    --output-file=web/assets/fonts/kvist-mono-latin-700.woff2 \
    --flavor=woff2 "--layout-features=*" --no-hinting --desubroutinize \
    --unicodes="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD,U+2500-257F,U+2580-259F,U+25A0-25FF,U+2022,U+00B7,U+2026,U+2190-2199,U+2713,U+2717,U+25CF,U+25CB"

Cyrillic subset:

  .venv/bin/python -m fontTools.subset \
    /usr/share/fonts/TTF/JetBrainsMono-Regular.ttf \
    --output-file=web/assets/fonts/kvist-mono-cyrillic-400.woff2 \
    --flavor=woff2 "--layout-features=*" --no-hinting --desubroutinize \
    --unicodes="U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116"

  .venv/bin/python -m fontTools.subset \
    /usr/share/fonts/TTF/JetBrainsMono-Bold.ttf \
    --output-file=web/assets/fonts/kvist-mono-cyrillic-700.woff2 \
    --flavor=woff2 "--layout-features=*" --no-hinting --desubroutinize \
    --unicodes="U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116"

File sizes (ls -l, bytes)
--------------------------
  kvist-mono-latin-400.woff2       26112
  kvist-mono-latin-700.woff2       26836
  kvist-mono-cyrillic-400.woff2     5416
  kvist-mono-cyrillic-700.woff2     5508
  -------------------------------------
  TOTAL                            63872  (62.4 KB, budget 90 KB / 92160 B)

Usage
-----
See font-face.css in this directory for ready-to-paste @font-face rules.
The Latin subsets load on every page; the Cyrillic subsets only load when
a glyph in their unicode-range is actually rendered (e.g. the Russian
page), thanks to the browser's unicode-range-driven lazy font fetch.
