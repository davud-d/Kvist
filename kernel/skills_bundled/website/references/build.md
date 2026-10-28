# Building the site

## Choose the simplest stack that fits

Default to plain files: `index.html`, one stylesheet, a little JavaScript only
where the page needs behaviour. Reasons that decide it for most small sites:

- It opens with a double click and works on any static host, so the person is
  not tied to a toolchain that will rot or to a hosting plan.
- Commands run by Kvist usually have no network (`AGENT_SANDBOX_NET` defaults
  to deny under Bubblewrap), so `npm install` of a framework often cannot even
  run here. A site that needs no install is a site you can verify.
- Fewer moving parts means less to explain in the report.

Reach for a framework or a static-site generator only when the interview showed
a reason: many pages sharing structure, content edited by someone who does not
code, a design system the project already uses. If the folder already holds a
framework, use it; do not introduce a second one.

## Structure

    index.html
    styles.css
    script.js            only if needed
    assets/              images, fonts, icons you made or were given

Multi-page sites repeat navigation by hand in plain HTML; say in the report
that this is the point at which a generator starts to pay off.

## Markup

- Real elements for real jobs: `header`, `nav`, `main`, `section`, `footer`,
  `button` for actions, `a` for navigation. Screen readers, search engines and
  keyboard users all read this structure; a pile of `div`s loses it for all
  three.
- One `h1`, headings in order, each section's heading stating its point (the
  `design` skill's `websites` page explains why).
- `<html lang="…">` set to the page's language, `meta viewport`, a `title` and
  `meta description` specific to the page, and Open Graph tags so a shared link
  has a preview. A favicon as an inline SVG data URL avoids an extra file.
- Every image has `alt` text that says what it shows for someone who cannot
  see it (empty `alt` for pure decoration) and explicit `width` and `height`,
  so the page does not jump while it loads.

## Styles

- Design tokens as CSS custom properties on `:root` (colours, spacing scale,
  type scale, radius). When the person asks for a change of palette it is one
  edit, and light and dark variants stay consistent.
- Mobile first: write the narrow layout, then widen with `min-width` queries.
  Most first visits to a small site happen on a phone.
- A system font stack or fonts you bundle in `assets/`. No external font or
  script CDN: a request to a third party leaks every visitor's address to it,
  breaks offline, and cannot be checked in a sandbox with no network.
- Respect `prefers-reduced-motion` and `prefers-color-scheme`.

## Content

- Write the real copy from the interview. Lorem ipsum hides the layout
  problems that real lengths cause (a long name, a two-line button, a
  Russian word twice the width of its English twin).
- For Russian text follow the `design` skill's `writing` page (quotes, dashes,
  non-breaking spaces before short words and units).
- Anything you do not know is a visible placeholder in square brackets, in the
  place it will appear. Never a plausible invention.
- Images: make them in SVG or CSS when the subject allows, use the person's own
  files, or leave a labelled placeholder box. Do not hot-link images from
  other sites and do not take pictures that belong to someone else.

## Forms

A static site cannot receive a submission. If the person wants a contact form,
say what it needs (a form service, a small server) and offer the choice,
instead of shipping a form that looks like it works and silently drops
messages. A `mailto:` or `tel:` link works today and is honest about what it
does.

## Keep to the request

Build the pages the interview settled and no more: no extra sections "for
completeness", no blog, no cookie banner nobody asked for. Extra surface is
extra to check, to translate and for the person to review. List ideas in the
report instead.
