#!/bin/sh
# Records every argument of every tmux call (one call per line, NUL between arguments, in
# $JQA_SHIM_LOG), then runs the real tmux with the same arguments and stdin.
# Fault injection for must-fail controls (calls look like: -S SOCK -f /dev/null SUBCMD ARG...):
#   JQA_SHIM_NOOP="clear-history|send-keys -R"  those calls succeed without doing anything
#   JQA_SHIM_KEEP_BUFFER=1                      paste-buffer runs without -d (the buffer is left behind)
for a in "$@"; do printf '%s\0' "$a" >> "$JQA_SHIM_LOG"; done
printf '\n' >> "$JQA_SHIM_LOG"
if [ -n "$JQA_SHIM_NOOP" ] && [ "$#" -ge 5 ]; then
  sub="$5"; sub2="$5 $6"
  old_ifs=$IFS; IFS='|'
  for pat in $JQA_SHIM_NOOP; do
    if [ "$pat" = "$sub" ] || [ "$pat" = "$sub2" ]; then IFS=$old_ifs; exit 0; fi
  done
  IFS=$old_ifs
fi
if [ -n "$JQA_SHIM_KEEP_BUFFER" ] && [ "$5" = "paste-buffer" ]; then
  s1="$1"; s2="$2"; s3="$3"; s4="$4"; shift 4
  n=$#; i=0
  while [ "$i" -lt "$n" ]; do a="$1"; shift; [ "$a" = "-d" ] || set -- "$@" "$a"; i=$((i+1)); done
  exec "$JQA_REAL_TMUX" "$s1" "$s2" "$s3" "$s4" "$@"
fi
exec "$JQA_REAL_TMUX" "$@"
