#!/bin/sh
# Stand-in for a remote-shell wrapper: fake-wrap.sh LOG FAKE_HOME COMMAND [ARG...]
# Records every argument it received (NUL separated) in LOG, then runs the command with a
# different HOME and a variable that exists only "inside".
log="$1"; fake_home="$2"; shift 2
: > "$log"
for a in "$@"; do printf '%s\0' "$a" >> "$log"; done
HOME="$fake_home" JQA_FAKE_HOME="$fake_home" JQA_REMOTE_VAR=inside exec "$@"
