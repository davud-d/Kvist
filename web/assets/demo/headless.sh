#!/usr/bin/env bash
# The headless guide: Kvist answering one-shot commands, no interface at all.
#
# Recorded as a plain shell session rather than through guide.py, because that
# is what this flow is — a script calling the agent. Every command here runs
# for real; none of them reaches a model.
#
# usage: headless.sh <repo> <home> <workspace>
set -u
repo=$1
home=$2
work=$3


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

run() {
    type_out "$1"
    ( cd "$repo" && HOME="$home" PATH="$repo/.venv/bin:$PATH" eval "$1" )
    printf '\n'
    sleep 1.6
}

sleep 1.0
run "python main.py --workspace $work --config-info | jq '{provider: .saved_provider, role: .saved_role}'"
run "python main.py --workspace $work --session-op status | jq '{active: .active_session, sessions}'"
run "python main.py --workspace $work --session-op create --session-name review | jq '{ok, active_session}'"
run "python main.py --workspace $work --journal-op changes | jq -r .report"
sleep 2.0
