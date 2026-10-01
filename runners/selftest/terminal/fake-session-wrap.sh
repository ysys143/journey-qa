#!/bin/sh
# Stand-in for a wrapper with a persistent connection: fake-session-wrap.sh STATE_DIR COMMAND [ARG...]
# "Group membership" is the file STATE_DIR/groups, read once when a connection starts and kept in
# STATE_DIR/session until the connection is restarted (the file is removed). Every command through
# the wrapper sees the membership captured at connection start, not the file's current content.
state="$1"; shift
[ -f "$state/session" ] || cp "$state/groups" "$state/session"
JQA_FAKE_GROUPS="$(cat "$state/session")" exec "$@"
