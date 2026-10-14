# Websites: landing pages, product sites, docs, portfolios, blogs

A website is read before it is used. It has seconds to say what it is, for
whom, and what to do next, to someone who arrived from a search result or a
shared link and may leave at any moment. It is also the first thing a future
user sees of a product, so it belongs to the first-run path (`first-run.md`).

## Landing and product pages

### The first screen

- Answer three questions without scrolling: **what is it** (in the visitor's
  words, not the category's jargon), **who is it for / what does it do for
  me**, **what do I do now** (one primary action).
- Show the real thing. A screenshot, a short recording or a live demo of the
  actual product beats an abstract illustration, because it answers "what
  does it look like" and proves it exists. For a developer tool, the install
  command and a real terminal recording are often the best hero.
- Open with the most characteristic thing in the subject's world. The
  default hero (big headline, subhead, two buttons, a gradient blob, three
  stat tiles) is generic; use it only when nothing specific to the subject
  would serve better.
- Size the hero to its content, not to `100vh`: a full-viewport opener pushes
  everything else out of the first view, and the first frame is what link
  previews and skimmers see.
- The headline is specific and short: "Run your test suite in parallel on
  your laptop" beats "Supercharge your workflow". No single word in italic or
  another colour as decoration.

### The story below it

- Order sections by the visitor's questions: what it does, how it works (in
  three concrete steps if it really is a sequence), what it looks like in
  use, why to trust it, what it costs, how to start.
- Each section makes one point, with a heading that states the point (not
  "Features") and evidence (a screenshot, a number, a code sample, a quote).
- Proof must be real: real customer logos only with permission, real quotes
  with real names, real numbers with their source. Invented testimonials and
  metrics are worse than none and may be unlawful.
- Repeat the primary action after the main argument and at the end, with the
  same label.
- Pricing: plans side by side, the recommended one marked, every limit
  stated, the currency and billing period explicit, "contact sales" only for
  what genuinely varies.
- FAQ answers the real objections (privacy, lock-in, offline use, platform
  support), not marketing questions nobody asks.

### Visual direction

- Choose from the subject: its materials, colours, documents, instruments and
  vocabulary. A site for a hardware tool can borrow its datasheets; one for a
  bakery its paper bags and stamps.
- Editorial pages earn a stronger typographic voice: a characterful display
  face used with restraint, a clear scale (ratio 1.25–1.5), generous space.
- One memorable idea, carried out precisely, and quiet everywhere else.
- Motion: one orchestrated moment (a page-load sequence, one interactive
  demo) at most. Content is visible at rest; nothing waits at opacity 0 for a
  scroll observer. Respect reduced motion. A zoom-in or long intro reads as
  slow and is skipped by impatient visitors.
- See the anti-generic list in `SKILL.md`; editorial pages are where those
  defaults are most tempting and most visible.

## Documentation sites

- Organise by what the reader is doing (the Diátaxis split works well):
  **tutorials** (learning, step by step, guaranteed to work), **how-to guides**
  (a goal, the steps, no theory), **reference** (complete, accurate,
  structured like the thing it describes), **explanation** (why it works this
  way). Mixing them makes each worse.
- The first page is a quickstart that reaches a working result in minutes,
  tested on a clean machine, with the expected output shown.
- Search that works on the first keystroke and finds API names, error
  messages and config keys.
- Every page: a clear title, a one-line summary, headings that can be linked
  (anchor links with `scroll-margin-top` under a sticky header), "previous /
  next" for sequences, the version it applies to, a link to edit or report.
- Code blocks: syntax highlighted with sufficient contrast in both themes, a
  copy button that copies only the command (no prompt `$`, no output), the
  language or filename shown, long lines scrolling inside the block rather
  than the page. Mark which parts the reader must replace
  (`YOUR_API_KEY`), and keep those placeholders consistent.
- Error messages from the product appear verbatim in the docs, so a search
  for the message finds the fix.
- Reading width about 70 characters for prose, wider for tables and code.
- Navigation: a sidebar with the current page highlighted and its section
  expanded, a table of contents for the current page on wide screens.

## Portfolios and blogs

- The work first: large images or demos, a short caption with the problem,
  the role and the result. Case studies tell the decision, not the process
  diary.
- Blog posts: a reading column of 60–75 characters, 17–20 px body text, a
  clear date and author, headings every few paragraphs, figures with captions,
  footnotes or sidenotes on wide screens, a way to subscribe (RSS at least).
- An index that can be scanned: titles, dates, one-line summaries, tags only
  when they help find things.

## Technical floor for every site

- **Metadata**: a unique `<title>` and `meta description` per page; Open Graph
  and Twitter card tags with a designed 1200×630 image; `link rel="canonical"`;
  a favicon set (SVG plus PNG sizes, `apple-touch-icon`); `lang` on `<html>`;
  `hreflang` for translated pages.
- **Discoverability**: `sitemap.xml`, `robots.txt`, real text (not text in
  images), meaningful link text ("Read the install guide", not "click here"),
  structured data where it applies (software, article, FAQ, breadcrumbs).
- **Performance budget**: decide one (for example, under 200 KB of JavaScript
  and LCP under 2.5 s on a mid-range phone) and check it on every change.
  Static HTML and CSS are the cheapest way to meet it. Self-host fonts where
  possible, subset them, and preload the one that renders the headline.
- **Responsive** from 320 px to ultra-wide; no horizontal page scroll; images
  with `srcset` and explicit dimensions.
- **Accessible**: the floor from `SKILL.md`, a skip link, headings in order,
  alt text, visible focus, captions for videos, reduced motion.
- **Consent and privacy**: no analytics or tracking cookies before consent
  where the law requires it; a privacy page that says what is collected;
  prefer privacy-respecting analytics.
- **404 and error pages**: say what happened, offer search and the main
  links, keep the site's navigation.
- **Footer**: what people look for there (docs, pricing, contact, legal,
  status, social links, language switch), nothing more.
- **Forms** (contact, newsletter): the rules in `interaction.md`; say what
  happens after submitting and when they will hear back.

## Checking a site

- Screenshot the first view at 1440×900 and 390×844 in both themes: can a
  stranger say what it is and what to do in five seconds?
- Read only the headings top to bottom: do they tell the story?
- Check the link preview image and title (paste the URL into a preview
  checker or render the OG tags).
- Run a performance audit (Lighthouse or the browser's performance panel)
  with mobile throttling, and an accessibility audit (axe or Lighthouse).
- Click every link and button; check the 404 page.
- Read the copy aloud: anything you would not say to a person, rewrite.
