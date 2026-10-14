# Writing in interfaces (English and Russian)

Interface text exists to help someone understand and act. It is read in a
glance, often half-read, often by someone stressed or in a hurry. In a CLI or
TUI the words are most of the interface. Write them with the same care as
the layout.

## Principles

- **The person's words, not the system's.** "Notifications", not "Webhook
  config"; "Your files", not "Blob storage"; "Couldn't connect to the
  internet", not "ECONNREFUSED".
- **Specific beats clever.** "Delete 3 files" beats "Clean up"; a joke in an
  error message the person sees every day stops being funny on the second day.
- **Front-load.** Put the information people scan for at the start of the
  sentence, label or list item: "Export as PDF", not "Use this to export the
  report as a PDF".
- **One word per concept, everywhere.** If it is a "project" on one screen,
  it is not a "workspace" on another. Keep a short glossary for the product.
- **Active voice, plain verbs.** "Kvist saved the file", or simply "Saved";
  not "The file has been successfully saved".
- **Short.** Cut "please", "simply", "just", "successfully", "in order to",
  "at this time". Every sentence does one job.
- **Sentence case** for buttons, headings and menu items (unless the
  platform or the product's established style says Title Case). Never ALL CAPS
  for sentences.
- **Numbers as digits** ("3 files", "8 deployments"), with units and a space
  (non-breaking) between number and unit.
- **Second person, present tense** for instructions ("Choose a folder").

## By element

### Buttons and links

- A verb plus an object when the object is not obvious: "Save changes",
  "Create project", "Install update". Not "OK", "Submit", "Yes", "Continue"
  where a specific verb exists.
- The confirmation reuses the verb: "Publish" → "Published".
- Links say where they go: "Read the install guide", not "click here" or
  "learn more" (alone, it means nothing to a screen reader's link list).
- Destructive buttons name the destruction: "Delete project", not "Confirm".

### Errors

Structure: **what happened**, **why** (if it helps the person act), **what to
do now**. In that order, in one or two sentences.

| Weak | Better |
| --- | --- |
| Error 403 | You don't have access to this project. Ask its owner (anna@…) to invite you. |
| Invalid input | Enter a date in the past, like 14.03.1990. |
| Something went wrong | Couldn't save: the server didn't respond in 30 s. Your changes are kept here. Try again |
| Network error | No internet connection. Kvist will retry when you're back online. |
| Failed to load models | Couldn't load the model list from OpenRouter: the key was rejected (401). Check the key in Settings → Providers. |

- No blame ("You entered an invalid…"), no vague apology ("Oops!"), no
  exclamation marks, no raw codes as the main text (keep codes for a details
  line, they help support).
- Say what is preserved ("Your changes are kept") when it is true; people
  fear losing work more than the error itself.
- The fix is an action when possible: a button "Try again", "Open settings",
  "Copy command".
- For things nobody can fix, be honest: "This is a bug in Kvist. The details
  were saved to ~/.kvist/logs/crash-1014.txt; please attach it to a report."

### Empty states

What this place is for, why it is empty, the action that fills it:
"No sessions yet. Your conversations appear here. Start one with a question
about this folder." With the button that starts one.

### Confirmations and warnings

- Name the object and the consequence: "Delete 'atlas'? This removes its
  history and can't be undone."
- Ask questions that can be answered by the buttons: the question "Discard
  changes?" with buttons "Discard" and "Keep editing", never "Yes" / "No".

### Success messages

Say what happened, specifically, and the next step if there is one:
"Connected to DeepSeek. Model: deepseek-chat." Not "Success!".

### Help and onboarding text

- Explain consequences, not mechanics: "Faster, but may need a second try"
  rather than "Uses the fast model tier".
- Teach in context, one tip at a time, dismissible, and only once.
- Never explain the interface's own layout ("Click the blue button at the
  top"); describe the action ("Choose New project").

### Labels, hints and placeholders

- A label names the field ("Email"); a hint below it explains format or
  purpose ("We'll send the receipt here"); a placeholder shows an example
  ("e.g. anna@example.com"). Never put the label or the instructions only in
  the placeholder.

### Loading and progress text

Say what is happening in the person's terms: "Downloading Node.js (28 MB)…",
"Checking the key…", "Indexing 1 240 files…". After a few seconds of no
change: "Still connecting… 8 s".

## Voice and tone

- **Voice** is constant (who the product is): for most tools, a competent,
  direct colleague. **Tone** shifts with the situation: neutral in routine,
  calm and precise in errors, warm but brief in success, serious in anything
  about money, data loss or security.
- Humour only where the person is relaxed and will not see it often, and never
  in errors, warnings or security text.
- No marketing voice inside the product ("Unlock the power of…").
- Avoid the mannerisms of generated text: "not X, but Y" framing, colon
  reveals, em-dash asides in every sentence, scare quotes, "seamless",
  "robust", "leverage", "delve", "it's worth noting".

## Russian interfaces

### Address and grammar

- Choose «ты» or «вы» once for the product and keep it everywhere. «Вы» is the
  default for most products; «ты» suits informal tools whose audience expects
  it. When «вы» is the form of address, it is written in lowercase in
  interfaces (capitalised «Вы» belongs to personal letters).
- Avoid gendered verb forms about the person ("Вы сохранили/сохранила"). Use
  impersonal and passive forms: «Сохранено», «Файл удалён», «Не удалось
  подключиться».
- Infinitive or imperative for buttons, consistently: «Сохранить», «Создать
  проект» (infinitive is the common Russian UI convention); not a mix of
  «Сохранить» and «Создай».
- Errors start with what failed: «Не удалось сохранить файл: нет места на
  диске. Освободите 200 МБ и попробуйте снова.»
- No calques: «Попробуйте снова», not «Попытайтесь опять»; «Настройки», not
  «Сеттинги»; keep established technical terms («коммит», «репозиторий»,
  «токен») when the audience uses them, and use one of them consistently.
- Do not translate product and command names; mark them so they are not
  translated (`translate="no"` on the web).

### Plural forms

Russian has three forms for integers, chosen by the last digits:

- **one**: 1, 21, 31, 101 (but not 11) → «1 файл», «21 файл»
- **few**: 2–4, 22–24, … (but not 12–14) → «2 файла», «23 файла»
- **many**: 0, 5–20, 25–30, 11–14, … → «5 файлов», «11 файлов», «0 файлов»
- fractions take the «few» form for nouns: «1,5 файла».

Use the platform's plural rules (`Intl.PluralRules('ru')`, gettext
`ngettext`, ICU MessageFormat `{count, plural, one {# файл} few {# файла} many
{# файлов} other {# файла}}`). Never build a plural by appending a letter, and
never write «файл(ов)».

### Typography

- Quotes: «ёлочки» as the main ones, „лапки“ inside them:
  «Нажмите „Сохранить“».
- Dash: an em dash with spaces around it (« — »), a non-breaking space
  before it so it never starts a line; an en dash without spaces for ranges
  (10–20, 2020–2026); a hyphen only inside words (что-то).
- Non-breaking spaces: after one- and two-letter words (в, к, с, и, не, на),
  between a number and its unit (10 МБ, 5 мин), in initials and surnames
  (А. С. Пушкин), before a dash.
- Numbers: thousands separated by a (thin or non-breaking) space, decimals
  with a comma: 12 345,67. Currency after the number: 1 500 ₽. Percent: both
  «15%» and «15 %» (with a non-breaking space) are in use; pick one for the
  product and keep it. `Intl.NumberFormat('ru')` does the separators for you.
- Dates: «14 октября 2026», «14.10.2026», 24-hour time «15:49». The week
  starts on Monday.
- The letter «ё» where it changes meaning or pronunciation (все/всё) and
  preferably everywhere in a product's own copy; search must treat ё and е as
  the same.
- Sentence case for headings and buttons; Russian has no Title Case.
- No full stop after headings, button labels, or single-sentence list items
  in a column; a full stop after a sentence in body text and tooltips.
- Abbreviations with the standard forms: МБ, ГБ, мин, с (seconds), ч; «т. е.»,
  «и т. д.» with spaces.

### Length and layout

- Russian text is commonly 15–35 % longer than English, and words are long
  («Синхронизировать» does not fit a button sized for "Sync"). Leave room,
  let buttons grow, allow wrapping in labels, test every screen in Russian.
- Hyphenation (`hyphens: auto` with `lang="ru"`) helps narrow columns.
- Fonts must have full Cyrillic with «ё» and «й», and the ₽ sign.

## Bilingual products

- Write the source strings in one language with keys and notes for
  translators (where the string appears, what the placeholders mean, the
  maximum length).
- Use placeholders, never concatenation: `"Deleted {count} files from
  {folder}"`, so word order can change.
- Detect the language from the system locale or `Accept-Language`, let the
  person switch, and remember the choice. Never guess from IP.
- Keep terminology tables per language and check them in review.
- Test with the longest language and with pseudo-localisation (accented,
  lengthened strings) to find hardcoded text and clipped layouts.
