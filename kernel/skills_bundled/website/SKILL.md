# Website

The person started this with `/website`. They want a working website made for
them. Most people who ask for one know how it should feel long before they can
say what it contains, so the job is to turn that feeling into a real site: ask
the few things only they can answer, build it, look at it in a browser, and
say plainly what you assumed.

This skill is the process. How a good site looks and reads (first screen,
section order, type, colour, anti-generic defaults) lives in the `design`
skill, and keeping it in one place means the two never disagree. Attach
`design` now and read its `websites` page before you choose a look.

## Why the work goes in this order

Each step exists because skipping it has a typical, expensive failure.

1. **Look before you ask.** A question the folder already answers (a framework
   in `package.json`, an `index.html`, brand colours in a stylesheet, a
   `README` that says what the project is) costs the person patience and tells
   them you did not look. Read first; ask about what is left.
2. **Ask what changes the build.** A site built on a wrong guess about its
   purpose or its visitor is rebuilt from the top, so a few questions up front
   are the cheapest part of the work. A question you could settle with a sound
   default is the most expensive kind: it hands the person a decision they did
   not want. Ask the first kind, decide the second and say so.
3. **Build, then look.** Code that compiles can still overflow a phone screen,
   vanish in the dark theme or link to a page that does not exist. The only
   evidence a site works is having opened it.
4. **Report what you assumed.** The person will publish this under their own
   name. Whatever you invented, borrowed or could not check is theirs to know.

## The steps

1. **Look.** List the folder and read what names the project: `README`,
   `package.json`, existing pages and styles, `KVIST.md`. Decide which of two
   jobs this is. A *new site* gets the whole process. A *change to an existing
   site* is narrow work: skip the interview, follow the project's own style
   (`design` says how), and change only what was asked.
2. **Interview** a new site. Read `references/interview.md` for the questions,
   their order and three worked examples. Ask with `ask_user`, one question
   per call, each with options and your recommended one first.
3. **Say it back.** Before building, write one short paragraph: who the site is
   for, what a visitor should do, the look in a few words, the structure. If
   the work is large enough that a wrong direction would cost a rebuild, this
   is the moment for `offer_plan` when the offer is available.
4. **Build.** Read `references/build.md`.
5. **Check.** Read `references/verify.md`. Do not call the work done before
   you have done what it describes, or said honestly which part you could not.
6. **Report** in the person's language: what exists and how to open it,
   what you assumed (each assumption in a few words), which text, images and
   contact details are placeholders they must replace, and what you did not
   do. Offer, never perform, publishing: putting a site on the internet is
   visible to others and hard to take back, so it happens only when asked.

## Principles that apply throughout

- **Speak the person's language.** These pages are in English; the person is
  not necessarily. Ask the questions, write the options and the report in the
  language they wrote the request in. The site's own language is a separate
  choice (question 5 of the interview): default it to the language the person
  writes in, and ask when the audience clearly reads another.
- **No invented proof.** Testimonials, customer logos, review scores, prices,
  addresses, phone numbers and statistics are either the person's real ones or
  visible placeholders such as `[Your phone number]`. A fake that looks real
  gets published by accident, and invented reviews and metrics may be unlawful.
- **Another site is a reference, not a source.** If the person says "like
  site X", take what the site teaches (what comes first, how formal the tone
  is, how dense) and write the structure, text, images and code yourself. The
  other site's text, images, code and name belong to its owner, and a page of
  the person's own content is also the better result.
- **When nobody can answer, decide.** `ask_user` answers `skipped` when the
  person passes and `no_interactive_user` when the run is headless. In both
  cases continue on a sensible default and list it in the report; do not stop
  to wait and do not ask again.
- **Existing files are the person's.** Read before you overwrite; put new work
  in new files when the folder already holds something that is not clearly
  yours to replace.

## Reference pages

Read a page when you reach its step, not all of them at the start: every page
you read stays in the conversation.

| Page | Read it when |
| --- | --- |
| `references/interview.md` | step 2: choosing and phrasing the questions |
| `references/build.md` | step 4: stack, structure, markup, content, forms |
| `references/verify.md` | step 5: serving the site, what to measure, what to report |
