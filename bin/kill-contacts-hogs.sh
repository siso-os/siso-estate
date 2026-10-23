#!/bin/bash
# kill-contacts-hogs — Shaan uses neither macOS Contacts nor iMessages; these
# daemons crash-loop on a stale AddressBook/Messages store and burn CPU.
# HARD ALLOWLIST: only these EXACT basenames can ever be killed. Never a substring.
# Guards: never touch pid<=1, never anything not in the list, only when over threshold.
THRESHOLD=${1:-40}   # % CPU trigger (raised from 25 — these hit 74%; 40 avoids twitchy kills)
LOG=~/Library/Logs/kill-contacts-hogs.log
ALLOW=("contactsd" "IMDPersistenceAgent" "contactsdonationagent")

is_allowed() { local b="$1"; for a in "${ALLOW[@]}"; do [ "$b" = "$a" ] && return 0; done; return 1; }

# iterate every process; match on EXACT basename only
ps -Ao pid=,pcpu=,comm= | while read -r pid cpu comm; do
  base="${comm##*/}"                       # basename of the executable path
  [ "$pid" -gt 1 ] 2>/dev/null || continue  # never pid 0/1
  is_allowed "$base" || continue            # HARD allowlist — exact match only
  # cpu is like 74.0 ; compare as int
  cpu_int=${cpu%.*}
  [ "${cpu_int:-0}" -gt "$THRESHOLD" ] 2>/dev/null || continue
  kill -9 "$pid" 2>/dev/null && echo "$(date '+%F %T') killed $base pid=$pid cpu=${cpu}% (>${THRESHOLD})" >> "$LOG"
done
