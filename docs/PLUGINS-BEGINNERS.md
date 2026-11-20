# Your own Kvist plugin in 5 minutes (for people who have never written code)

Russian version: [PLUGINS-BEGINNERS.ru.md](PLUGINS-BEGINNERS.ru.md). Full description: [PLUGINS.md](PLUGINS.md).

## What a plugin is

A plugin is your own command for Kvist. For example, you type `/simple` and Kvist starts explaining everything
in very simple words. Or you type `/novibecode` and Kvist stops writing code for you and starts teaching.

A plugin is a folder with one small text file in it. You don't need to install anything.

For now, plugins run only on Linux.

## Step 1. Find the folder

Open your project folder, the one you start Kvist in. Create a folder named `plugins` inside it.

```
my-project/
  plugins/      ← this one
```

Want the command to work in every project at once? Then create the `plugins` folder here:

* Linux and macOS: `~/.kvist/plugins/` (`~` is your home folder)
* Windows: `C:\Users\YourName\.kvist\plugins\`

## Step 2. Create a folder for your command and a file in it

Inside `plugins`, create a folder named `simple`. Any folder name works, as long as it tells you what is inside.

Inside `simple`, create a file named exactly `plugin.py`. Any text editor will do: VS Code, Notepad, anything.
Just make sure the file is really `plugin.py`, not `plugin.py.txt` and not `simple.py`.

```
my-project/
  plugins/
    simple/
      plugin.py     ← write here
```

Each command lives in its own folder. Want a second command? Make a second folder next to it.

## Step 3. Paste this into it

```python
"""Explain it like I'm five."""


def command():
    return "simple"


def run(request):
    return "Explain in very simple words, with no jargon, using an everyday example."
```

Save the file.

What each part is:

| Line | What it means | What you can change |
| --- | --- | --- |
| `"""Explain it like I'm five."""` | the command's label in the menu | any text |
| `return "simple"` | the command's name: `/simple` | lowercase letters, digits and `-` |
| `return "Explain ..."` | what Kvist should do | any text; this is your instruction |

Leave everything else (`def`, `run(request)`, the colons, the indentation) as it is. Indentation is the 4 spaces
at the start of a line; don't delete them.

## Step 4. Restart Kvist

Close Kvist and open it again. The command menu is built at start-up.

## Step 5. Try it

Type `/si` and `/simple` shows up in the list. Add a question and press Enter:

```
/simple what is an API?
```

The first time, Kvist asks: "Run the plugin /simple?". Say yes. This is a safeguard: Kvist does not run files
without asking. It won't ask again unless you change the file.

Done, you have your own plugin.

## A long instruction in its own file

A big instruction is awkward to write inside quotes. Put it in a plain text file in the same folder instead.

```
plugins/
  novibecode/
    plugin.py
    novibecode.md
```

`plugins/novibecode/novibecode.md`:

```markdown
Help the person write the code themselves. Do not write the code for them, not even a piece.
Explain the approach in simple words, ask one leading question
and say which documentation page to read.
```

`plugins/novibecode/plugin.py`:

```python
"""Teach me instead of writing the code."""
import kvist


def command():
    return "novibecode"


def run(request):
    return kvist.read("novibecode.md")
```

`kvist.read("novibecode.md")` means "take the text of the file novibecode.md in this same folder".

You can change the instructions in the `.md` as often as you like: Kvist won't ask for permission every time,
because it is just text, not code.

## A little smarter: a command that looks at what you typed

This is the file `plugins/review/plugin.py`. `request` is what you typed after the command. If you wrote `/review main.py`, `request` is `"main.py"`. If you
wrote just `/review`, `request` is empty.

```python
"""Review my code and find the mistakes."""
import datetime


def command():
    return "review"


def run(request):
    if request == "":
        return "Ask the person which file to review."
    today = datetime.date.today().strftime("%d.%m.%Y")
    return "Today is " + today + ". Review the file " + request + " and find the mistakes. Do not fix anything yourself."
```

If `request` is empty, Kvist asks which file to review. Otherwise it gets an instruction with today's date and
the file name.

## Hints in the editor

If you have the Kvist extension in VS Code or JetBrains, it suggests plugin code in any file in the `plugins`
folder. Start typing and it offers what comes next.

## Something doesn't work

| What you see | What to do |
| --- | --- |
| The command is not in the menu | Restart Kvist. Check that the file is in `plugins/your-folder/` and is named exactly `plugin.py`, not `plugin.py.txt` |
| `Plugin skipped: ... a plugin lives in its own folder` | The file lies directly in `plugins`. Make a folder for it and rename it to `plugin.py`, as the message says |
| `... must be named plugin.py` | The file in the folder has another name, like `simple.py`. Rename it to `plugin.py` |
| `Plugin skipped: ... must only return the name as plain text` | The name in `command()` must be in quotes: `return "simple"` |
| `... use lowercase letters` | The name may only have lowercase letters, digits and `-`: `my-helper`, not `My Helper` |
| `... is a built-in Kvist command` | Kvist already uses that name (for example `new` or `help`). Pick another one |
| `... syntax error on line 5` | There is a typo on line 5, usually a missing quote, a missing colon or lost indentation |
| `... no command()` or `no run(request)` | One of the two functions got deleted. Copy the example again |
| `/simple failed: ...` | Something went wrong inside `run`. The last line of the message says what |
| `... cannot run here` | Plugins run only on Linux for now; on other systems they cannot run |

## Can a plugin break my computer?

No. A plugin runs inside a closed "box": it sees only its own folder and has no access to other plugins, the
internet, your files or your passwords. So even a plugin from someone else's project cannot do anything bad.
And before its first run, Kvist asks you anyway.
