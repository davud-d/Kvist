This file belongs to a Kvist plain plugin: a folder inside a `plugins` folder (`<project>/plugins/<folder>/` for one project, `~/.kvist/plugins/<folder>/` for all) whose `plugin.py` adds one slash command to Kvist. Other `.py` files in that folder are the plugin's own helper modules, imported from `plugin.py` by their plain name (`import helpers`). This is the whole API; complete against it, never against a guessed one.

Two functions, both required, at the top level of `plugin.py`:

- `def command():` takes no arguments and only returns the command's name as a literal string: `return "novibecode"`. Lowercase letters, digits and `-`. Kvist reads this string from the source without running the file, so nothing else may stand in the function: no variable, no f-string, no computation.
- `def run(request):` takes exactly one argument, the text the person typed after the command (`/novibecode explain loops` gives `"explain loops"`, a bare `/novibecode` gives `""`), and returns a `str`: instructions for Kvist's model for this request. Any other return type is an error.

The module docstring (first line) is the command's description in the `/` menu.

`import kvist` gives one function:

- `kvist.read(name)` returns the text of a file in the plugin's own folder as `str`, e.g. `kvist.read("novibecode.md")` or `kvist.read("prompts/short.md")`. Use it to keep long instructions in Markdown files next to `plugin.py`.

There is nothing else in `kvist`: no `kvist.workspace`, no `kvist.ask`, no `kvist.send`, no decorators, no classes to subclass, no registration call.

`run` executes in a sandbox: the Python standard library works, the plugin's own folder is readable, everything else is not there. No network, no project files, no home folder, no environment variables, no third-party packages; a `print` goes to a log, not to Kvist. It has 30 seconds.

Typical `plugins/novibecode/plugin.py`:

```python
"""Teach instead of writing the code."""
import kvist


def command():
    return "novibecode"


def run(request):
    return kvist.read("novibecode.md") + "\n\nThe person asks: " + request
```
