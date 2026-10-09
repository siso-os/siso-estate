#!/bin/zsh
# Run over ssh from the laptop (com.siso.estate-mini-codex-vault): launchd on the mini may not write the external drive.
# Move finished Codex rollouts (idle over an hour, held by no process) off the mini's internal disk to its encrypted vault
# drive, rsync -c verified before each source goes. Hourly from the mini's com.siso.estate-storage-guard job.
# 8 Oct: 465 Codex runs in a day wrote 17 GiB of rollouts on the mini (internal disk 1.7 GiB free at 20:55).
# To resume an old session: copy its file back from the vault to the same path under ~/.codex/sessions.
set -u
S=$HOME/.codex/sessions; V=/Volumes/SISO-STORAGE-VAULT/cold-storage/mini-codex-sessions
LOG=$HOME/.local/state/estate/codex-sessions-to-vault.jsonl
mount | grep -q 'on /Volumes/SISO-STORAGE-VAULT (' || { echo "vault not mounted: nothing moved"; exit 0; }
[ -d $S ] || exit 0
mkdir -p $V ${LOG:h}; T=$(mktemp -d)
b=$(df -k /System/Volumes/Data | tail -1 | awk '{print $4}')
# Codex's thread-history projection (thread_history_*.sqlite, a copy of every chat's items; 3.4 -> 12 GiB on 8 Oct) goes
# too, whole, when it is over 4 GiB and no Codex runs: Codex starts a new one. To get it back: copy it home with Codex closed.
for P in $HOME/.codex/thread_history_*.sqlite(N); do
  [ $(stat -f %z $P) -gt 4294967296 ] || continue
  pgrep -qx codex && { echo "codex running: ${P:t} stays"; continue; }
  lsof -- $P $P-wal >/dev/null 2>&1 && { echo "${P:t} open: stays"; continue; }
  H=${V:h}/mini-codex-thread-history/$(date +%Y%m%d-%H%M); mkdir -p $H
  nice -n 19 rsync -a $P(N) $P-wal(N) $P-shm(N) $H/ && cmp -s $P $H/${P:t} && { rm -- $P(N) $P-wal(N) $P-shm(N); echo "moved ${P:t} to $H"
    printf '{"at":"%s","moved_projection":"%s","to":"%s"}\n' "$(date +%FT%T%z)" "${P:t}" "$H" >> $LOG; }
done
cd $S && find . -type f -name '*.jsonl' -mmin +60 | sort > $T/old
lsof -Fn 2>/dev/null | grep "^n$S/" | sed "s#^n$S/#./#" | sort -u > $T/open
comm -23 $T/old $T/open > $T/move
n=$(wc -l < $T/move | tr -d ' '); [ "$n" -gt 0 ] || { rm -r $T; exit 0; }
nice -n 19 rsync -a --files-from=$T/move . $V/ || echo "copy partly failed: only verified files are removed"
# a file whose copy differs (still being written) stays; every verified one goes
nice -n 19 rsync -ac --dry-run --out-format='./%n' --files-from=$T/move . $V/ | sort -u > $T/differ
comm -23 $T/move $T/differ > $T/gone
d=$(wc -l < $T/differ | tr -d ' '); n=$(wc -l < $T/gone | tr -d ' ')
while read f; do rm -- "$f"; done < $T/gone
[ -f $S/OLDER-IN-VAULT.md ] || printf '# Finished Codex sessions\n\nRollouts idle over an hour move to %s hourly (tools/codex-sessions-to-vault.sh). Copy one back here to resume it.\n' "$V" > $S/OLDER-IN-VAULT.md
a=$(df -k /System/Volumes/Data | tail -1 | awk '{print $4}')
printf '{"at":"%s","moved":%s,"kept_differ":%s,"to":"%s","free_gain_kib":%s}\n' "$(date +%FT%T%z)" "$n" "$d" "$V" "$((a-b))" >> $LOG
rm -r $T; echo "moved $n rollouts, +$(( (a-b)/1048576 )) GiB"
