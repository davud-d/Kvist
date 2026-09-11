#!/usr/bin/env bash
# The headless guide: Kvist answering one-shot commands, no interface at all.
#
# Recorded as a plain shell session rather than through guide.py, because that
# is what this flow is — a script calling the agent. Every command here runs
# for real; none of them reaches a model.
#
# usage: headless.sh <repo> <home> <workspace> [shown-as]
set -u
repo=$1
home=$2
work=$3
# The recording is published, so the screen shows a stand-in path rather than
# wherever this actually ran. The commands are written against it and rewritten
# to the real path on the way to the shell.
shown=${4:-$work}


type_out() {
    # Echo the command the way a person types it, then run it.
    printf '\033[92m$\033[0m '
    local text="$1"
    for (( i = 0; i < ${#text}; i++ )); do
        printf '%s' "${text:i:1}"
        sleep 0.018
    done
    printf '\n'
    sleep 0.35
}

# Each line is a pipeline, so a shell has to parse one. `sh -c` parses the
# literal written below and nothing else; `eval` would have parsed it in this
# script's own scope, where $repo, $home and $work are in reach.
run() {
    type_out "$1"
    ( cd "$repo" && HOME="$home" PATH="$repo/.venv/bin:$PATH" sh -c "${1//$shown/$work}" )
    printf '\n'
    sleep 1.6
}

sleep 1.0
run "python main.py --workspace $shown --config-info | jq '{provider: .saved_provider, role: .saved_role}'"
run "python main.py --workspace $shown --session-op status | jq '{active: .active_session, sessions}'"
run "python main.py --workspace $shown --session-op create --session-name review | jq '{ok, active_session}'"
run "python main.py --workspace $shown --journal-op changes | jq -r .report"
sleep 2.0
