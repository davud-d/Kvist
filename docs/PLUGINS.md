# Kvist plugins: your own slash command in one file

Russian version: [PLUGINS.ru.md](PLUGINS.ru.md). Never written code? Start with the step-by-step
[beginner guide](PLUGINS-BEGINNERS.md).

A plugin is one folder with a `plugin.py` in it. Put the folder into `plugins` and Kvist has a new command. You need no manifest
and no build step, and you don't need to know how Kvist works inside. For bigger extensions (tools the model
can call, network, a screen), see [SKILLS.md](SKILLS.md).

## Where the folder goes

```
plugins/
  novibecode/
    plugin.py        the command (required, exactly this name)
    novibecode.md    its instructions (any files you like)
    helpers.py       your own module, if you want one: import helpers
  review/
    plugin.py
```

| `plugins` folder | The commands work in |
| --- | --- |
| `plugins/` in your project | this project only |
| `~/.kvist/plugins/` | every project |

If both have a command with the same name, the project's one wins. Each plugin sees only its own folder, so
two plugins cannot read each other's files.

## The whole API: two functions

```python
# plugins/novibecode/plugin.py
"""Teach instead of writing the code."""
import kvist


def command():
    return "novibecode"


def run(request):
    return kvist.read("novibecode.md") + "\n\nThe person asks: " + request
```

```markdown
<!-- plugins/novibecode/novibecode.md -->
Help the person write the code by hand. Do not write it for them: give hints,
explain the approach, ask leading questions, point at the documentation.
```

* `command()` returns the command's name: `/novibecode`. It must be plain text in quotes (lowercase letters,
  digits, `-`), because Kvist reads it without running your file.
* `run(request)` gets what you typed after the command (`/novibecode loops` gives `"loops"`, a bare
  `/novibecode` gives `""`) and returns text. That text goes to Kvist's model as instructions for this request.
* The first line of the file's docstring is the description shown in the `/` menu.
* `import kvist` is optional. It has one function, `kvist.read("file.md")`, which returns the text of a file
  in the plugin's folder (`kvist.read("prompts/short.md")` works too).
* Other `.py` files in the folder are your own modules: `import helpers` in `plugin.py` finds `helpers.py`.

`run` can hold any logic: pick instructions by the request, add today's date, combine several files. Whatever
it returns is what Kvist reads.

## Safety

* Kvist asks before a plugin runs for the first time: "Run the plugin /novibecode?", with the folder, its
  Python files and one SHA-256 over all of them. The answer is remembered for that exact code in that project.
  When a `.py` file in the folder changes or a new one appears, Kvist asks again; editing the `.md`
  instructions does not.
* `run` executes in a sandbox. It sees a copy of its own folder, made at the moment you approved it, and the
  Python standard library, and nothing else: no other plugin, no network, no project files, no home folder,
  no keys, no environment variables. A plugin that arrives with
  a cloned repository can do nothing just by being there.
* `run` has 30 seconds. A `print` in it goes to a log and does not break anything.
* Running plugins needs the Linux sandbox (Bubblewrap and the system `/usr/bin/python3`). On other systems Kvist
  lists the command but says why it cannot run it.

## When something is wrong

A plugin that declares no usable command (no `command()` or `run`, a name that is not plain text, a built-in
name like `/new`, a syntax error) is skipped, and the terminal shows one line saying why. So is a `.py` file
lying loose in `plugins/` and a folder whose Python file is not named `plugin.py`: the line says where to move
or how to rename it. An error inside `run`
is shown in place of the answer, with the last lines of the traceback.

## Autocomplete

When you edit a Python file in a plugin's folder (`plugins/<name>/`), or a file that has `import kvist`, Kvist's inline completion in the
IDE knows this API. It suggests `return "name"` in `command()` and `kvist.read(...)` in `run`, and it does not
invent functions that do not exist.

## Interfaces

The plugin commands appear in the `/` menu of the Ink and Claude-style terminals, OpenCode and Classic (Glass).
In Classic Legacy you just type them. The core expands the command, so any interface that sends the typed line
as a request can run it.
