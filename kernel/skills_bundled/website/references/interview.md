# The interview

The interview exists to learn what only the person knows. Every question costs
them effort, so each one has to earn its place: it must change what you build,
and the folder, the request and a sound default must not already answer it.

## How to ask

- One `ask_user` per call. The next question often depends on the last answer,
  and a single answer is easy to give while a form of six is not.
- Give options, your recommendation first, so the common case is one keypress.
  Leave `allow_text` on: the person's own words are often better than your
  options, and "Other" is how they say so.
- Ask at most six questions. Stop earlier the moment the remaining answers
  would not change the build. Past six, people stop answering carefully.
- Phrase a question in the person's vocabulary. "What should a visitor do
  here?" works; "Define the primary conversion goal" does not.
- Answer by example when taste is involved. Taste is hard to describe and easy
  to recognise, so offer two or three concrete directions rather than asking
  "what style do you like?".
- Record nothing the person did not say. An answer you inferred is an
  assumption, and assumptions go in the final report.

## The questions, in this order

Skip any the request or the folder has already answered.

1. **What is the site for, and what should a visitor do?** (Book a table, write
   to me, buy, read, download, see my work.) This decides the first screen and
   the one primary action every section points back to.
2. **Who visits, and what do they already know?** A neighbour looking for
   opening hours and an engineer evaluating a tool need different words and
   different proof. This sets vocabulary, density and what to show first.
3. **What content exists?** Text, photos, logo, prices, a menu, a portfolio
   list. Real content shapes the layout; missing content becomes visible
   placeholders (see `SKILL.md`), and the person should hear that before they
   see it.
4. **How should it feel?** Offer three directions drawn from their subject, not
   from a template list, plus "like a site I'll name". A bakery might be warm
   and paper-like, plain and fast, or playful; a security tool calm and
   technical, bold, or editorial.
5. **How many pages, in which languages?** One scrolling page is the right
   default for most small sites. More pages or more languages multiply the
   work and the checking, so the person should choose them knowingly.
6. **Where will it live, and who will edit it?** This picks the stack. Static
   files fit a host that serves files and a person who edits text rarely. A
   need for forms that send mail, payments, accounts or editing by someone who
   does not code changes the answer, and is worth knowing before you build
   rather than after.

## Worked examples

<example>
Request: `/website` and nothing else, in an empty folder.

Nothing is known, so start with question 1 and let the answer reduce the rest.

    ask_user: "What is the site for, and what should a visitor do on it?"
    options: ["Show my work and let people contact me", "Sell or take
              bookings for a business", "Explain and download a software
              project", "Something else"]

The person picks the first. Question 2 is now mostly answered ("people who
might hire me"), so ask only who: clients, employers or other developers.
Then 3, 4 and 5; question 6 is skipped because a portfolio is a static site
by default, and the report says so.
</example>

<example>
Request: `/website a landing page for my command-line tool`, in a folder with a
`README.md`, a `LICENSE` and a `docs/` directory.

The README already says what the tool does and who it is for, so questions 1
and 2 are answered from it. Do not ask them.

    ask_user: "Which feel fits the page?"
    options: ["Calm and technical: monospace accents, a real terminal
              recording up top", "Editorial: large type, lots of space",
              "Plain and fast: almost no styling"]

Then one question about content (is there a recording or a logo?). Two
questions, then build. Say back: "Landing for <tool>, aimed at developers, one
action: install. Calm and technical."
</example>

<example>
Request: `/website make the contact section clearer`, in a folder that holds a
working site.

This is a change to an existing site, not a new one. No interview. Read the
contact section and the styles it uses, change that section in the project's
own style, and list anything else you noticed in the report without touching
it.
</example>

## Headless runs

With no interactive person, `ask_user` returns `no_interactive_user`. Do not
retry and do not end the turn on a question you could settle yourself. Choose
the recommended option of each question you would have asked, build, and list
those choices as assumptions at the top of the report so the person can
correct them in one message.
