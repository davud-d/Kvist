#!/usr/bin/env fish
#
# Re-record the two landing-page demo casts.
#
#   ./record.fish            # record both locales
#   ./record.fish en         # record one locale
#
# Requirements: fish, python3, asciinema 2.x, a configured Kvist provider
# (`kvist` reads ~/.kvist/config.json; this script never touches the key).
#
# The run is driven by drive.py, which types the goal into the prompt_toolkit
# TUI over a pty and mirrors everything asciinema records. Nothing is edited
# afterwards: whatever the agent did is what the site shows.

set -l here (dirname (status --current-filename))
set -l repo (realpath $here/../../..)
set -l locales $argv
test (count $locales) -eq 0; and set locales en ru

# Workspaces go under the per-user runtime directory rather than straight into
# /tmp: the path is predictable and the script starts by deleting it, which on a
# shared machine is somebody else's opportunity. /run/user/<uid> is 0700 and
# nobody else can plant anything in it. record-guides.fish reads the same root.
if test -z "$XDG_RUNTIME_DIR"
    echo "XDG_RUNTIME_DIR is not set — no private directory to record in"; exit 1
end
set -l root $XDG_RUNTIME_DIR/kvist-record
mkdir -p $root; or exit 1

set -l rec asciinema
if not command -q asciinema
    echo "asciinema not found — install it (pip install asciinema)"; exit 1
end

for loc in $locales
    set -l work $root/demo-$loc
    rm -rf $work
    python3 $here/fixture.py $work $loc; or exit 1

    echo "recording $loc -> $here/run-$loc.cast"
    env COLUMNS=100 LINES=32 TERM=xterm-256color \
        $rec rec --overwrite --cols 100 --rows 32 \
        --title "Kvist demo ($loc)" \
        -c "python3 $here/drive.py $repo $work $loc" \
        $here/run-$loc.cast; or exit 1

    node $here/../../tools/cast-compile.mjs $here/run-$loc.cast $here/run-$loc.json; or exit 1
end

echo "done"
