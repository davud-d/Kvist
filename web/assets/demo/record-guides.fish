#!/usr/bin/env fish
#
# Re-record the guide casts for the /guides/ page.
#
#   ./record-guides.fish              # all twelve
#   ./record-guides.fish role effort  # named ones
#
# Unlike record.fish, these need no provider account and no API key. Every guide
# runs under a throwaway HOME below $XDG_RUNTIME_DIR, so the real
# ~/.kvist/config.json is neither read nor written, and guide.py hands the child
# a short environment so an exported key cannot be picked up in its place.
#
# Kvist asks its provider for a model list on every launch, so the recordings
# need something that answers. stub_gateway.py is that something: a real, tiny
# OpenAI-compatible gateway on 127.0.0.1:20129, which is what OmniRoute — the
# built-in "local OpenAI-compatible gateway" — is pointed at here. It listens on
# 20129, not OmniRoute's default 20128, so a gateway you already run is neither
# disturbed nor recorded.
#
# Requirements: fish, python3 with pexpect, asciinema 2.x, node.

set -l here (dirname (status --current-filename))
set -l repo (realpath $here/../../..)
set -l web (realpath $here/../..)

set -l guides $argv
test (count $guides) -eq 0; and set guides provider custom models changeapi \
    commands role effort style toggles session changes headless

if not command -q asciinema
    echo "asciinema not found — install it (pip install asciinema)"; exit 1
end

# Same private root as record.fish, and for the same reason: these paths are
# predictable and get deleted on every run, which in a shared /tmp is an
# invitation. /run/user/<uid> is 0700.
if test -z "$XDG_RUNTIME_DIR"
    echo "XDG_RUNTIME_DIR is not set — no private directory to record in"; exit 1
end
set -l root $XDG_RUNTIME_DIR/kvist-record
mkdir -p $root; or exit 1

set -l log $root/guide.log
set -l port 20129
rm -f $log

# The gateway the recordings talk to. Started here, stopped on the way out.
python3 $here/stub_gateway.py $port &
set -l gateway $last_pid
sleep 1
function __kvist_stop_gateway --on-event fish_exit --inherit-variable gateway
    kill $gateway 2>/dev/null
end



for guide in $guides
    # Its own HOME and its own workspace, so the order guides are recorded in
    # cannot change what any of them shows.
    set -l gh $root/gh-$guide
    set -l work $root/gw-$guide
    rm -rf $gh $work

    # Only the provider guide shows the first-run picker; the rest open on a
    # configured agent, the way a returning user does.
    if test "$guide" = provider
        python3 $here/seed-config.py $gh first-run $port; or exit 1
    else
        python3 $here/seed-config.py $gh ready $port; or exit 1
    end

    # changes and headless need a workspace that already carries a journal from
    # a real run. record.fish leaves one in the same root.
    if contains $guide changes headless
        if not test -d $root/demo-en/.kvist
            echo "no journal to show — run ./record.fish en first"; exit 1
        end
        cp -r $root/demo-en $work
    else
        mkdir -p $work
    end

    echo "recording $guide"
    if test "$guide" = headless
        env COLUMNS=100 LINES=32 TERM=xterm-256color \
            asciinema rec --overwrite --cols 100 --rows 32 \
            --title "Kvist guide: $guide" \
            -c "bash $here/headless.sh $repo $gh $work /tmp/kvist-hl-ws" \
            $here/guide-$guide.cast; or exit 1
    else
        env COLUMNS=100 LINES=32 TERM=xterm-256color \
            asciinema rec --overwrite --cols 100 --rows 32 \
            --title "Kvist guide: $guide" \
            -c "python3 $here/guide.py $repo $gh $work $guide $log" \
            $here/guide-$guide.cast; or exit 1
    end

    node $web/tools/cast-compile.mjs $here/guide-$guide.cast $here/guide-$guide.json; or exit 1
end

kill $gateway 2>/dev/null

if test -s $log
    echo "driver warnings:"; cat $log
end
echo "done — rebuild the site with: cd $web; and node build.mjs"
