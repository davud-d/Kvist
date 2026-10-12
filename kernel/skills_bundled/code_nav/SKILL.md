# Python code navigation

Static analysis of the workspace's Python files with jedi and pyflakes. It
covers Python only; for other languages use search_code.

- Positions are 1-based lines and 0-based columns, the column of any character
  of the name (read the file first to get them).
- `code_nav__definition` follows imports to where a name is defined;
  `code_nav__references` lists every usage across the project;
  `code_nav__outline` gives a file's classes and functions with lines.
- `code_nav__rename_preview` returns one unified diff per file and writes
  nothing. Apply the change with your ordinary edits (replace_in_file), then
  run the tests: the preview cannot see names built at run time (getattr,
  strings), so check them.
- `code_nav__diagnostics` reports pyflakes findings: undefined names, unused
  imports, syntax errors. It does not run the code or check types.

The first call copies the project's Python files into the skill's sandbox
(protected files are never copied); a large project takes longer then.
