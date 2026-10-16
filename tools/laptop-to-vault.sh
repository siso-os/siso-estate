#!/bin/sh
# Cold laptop data to the Mac mini's encrypted vault drive, in two steps so a slow uplink never holds the disk hostage.
#
#   laptop-to-vault.sh pack PATH NAME [WHY]           folder or file -> OUTBOX/<date>-NAME.tar.zst, then PATH removed
#   laptop-to-vault.sh pack-old ROOT NAME DAYS [WHY]  only ROOT's *.jsonl older than DAYS (Claude transcripts)
#   laptop-to-vault.sh ship                           every archive in OUTBOX -> the mini, verified, then local copy removed
#
# pack: sha256 of the tar stream must equal the sha256 of the decompressed archive and a second read of the source (a
# change during packing aborts); a <PATH>.IN-VAULT.md pointer (folders and files) or ROOT/OLD-TRANSCRIPTS-IN-VAULT.md
# (pack-old) names the archive and the restore; a receipt goes to machines/laptop/removed.jsonl. zstd shrinks text
# 4-8x, so packing frees most of the space at once (15 Oct: uplink 360 KiB/s, mini 300 ms away). ship: the archive's
# sha256 must match on the mini before the local copy goes. Refuses HALO (ADR 0007: never off its machine). Cold means no
# write in 72 h (`find PATH -mmin -4320`) and no process (`lsof +D PATH`): check before pack.
set -u
OUTBOX="$HOME/SISO_Workspace/_data/vault-outbox"; V=/Volumes/SISO-STORAGE-VAULT/cold-storage/laptop
RECEIPTS="$(cd "$(dirname "$0")/.." && pwd)/machines/laptop/removed.jsonl"
free_() { df -h /System/Volumes/Data | awk 'NR==2{print $4}'; }
halo() { case "$1" in *[Hh][Aa][Ll][Oo]*|*[Oo]racle*) echo "REFUSED $1: HALO stays on its machine (ADR 0007)"; exit 2;; esac; }
receipt() { printf '{"at":"%s","what":"%s","path":"%s","bytes":%s,"to":"%s","why":"%s","proof":"%s","by":"ESTATE"}\n' \
  "$(date +%FT%T%z)" "$1" "$2" "$3" "$4" "$5" "$6" >> "$RECEIPTS"; }
pointer() { printf '# %s is packed for the Mac mini vault\n\nPacked %s by ESTATE (%s); sha256 of the tar %s, %s KiB before packing.\nArchive: `%s` until shipped, then mac-mini `%s` (ship: `tools/laptop-to-vault.sh ship`)\nRestore: `zstd -dc ARCHIVE | tar -C %s -xf -` (from the mini: `ssh mac-mini-herdr cat %s | zstd -dc | tar -C %s -xf -`)\n' \
  "$1" "$(date +%F)" "$2" "$3" "$4" "$5" "$V/$(basename "$5")" "$6" "$V/$(basename "$5")" "$6" > "$7"; }
mkdir -p "$OUTBOX"
case "${1:-}" in
pack)
  SRC="${2%/}"; NAME="$3"; WHY="${4:-laptop data disk under its floor}"; halo "$SRC"; [ -e "$SRC" ] || { echo "NO SUCH $SRC"; exit 2; }
  P=$(dirname "$SRC"); B=$(basename "$SRC"); K=$(du -sk "$SRC" | cut -f1); F="$OUTBOX/$(date +%F)-$NAME.tar.zst"
  [ -e "$F" ] && { echo "EXISTS $F"; exit 2; }
  tar -C "$P" -cf - "$B" | zstd -q -6 -T0 > "$F"
  Z=$(zstd -dc "$F" | shasum -a 256 | cut -c1-64); L2=$(tar -C "$P" -cf - "$B" | shasum -a 256 | cut -c1-64)
  [ "$Z" = "$L2" ] || { echo "VERIFY FAIL $SRC (changed or bad archive)"; rm -f "$F"; exit 1; }
  pointer "$B" "$WHY" "$Z" "$K" "$F" "$P" "$SRC.IN-VAULT.md"; rm -r "$SRC"
  receipt "packed for the mini vault drive (tar.zst, outbox)" "$SRC" $((K*1024)) "$F" "$WHY" "sha256 of tar stream equal to decompressed archive and second read: $Z"
  echo "PACKED $SRC $((K/1024)) MiB -> $(du -m "$F" | cut -f1) MiB; free $(free_)";;
pack-old)
  ROOT="${2%/}"; NAME="$3"; DAYS="$4"; WHY="${5:-laptop data disk under its floor}"; halo "$NAME"
  F="$OUTBOX/$(date +%F)-$NAME.tar.zst"; LIST="$OUTBOX/.$NAME.list"; [ -e "$F" ] && { echo "EXISTS $F"; exit 2; }
  (cd "$ROOT" && find . -type f -name '*.jsonl' -mtime +"$DAYS" | sort) > "$LIST"; N=$(wc -l < "$LIST" | tr -d ' ')
  [ "$N" -gt 0 ] || { echo "NOTHING $ROOT"; rm -f "$LIST"; exit 0; }
  K=$(cd "$ROOT" && tr '\n' '\0' < "$LIST" | xargs -0 du -sk | awk '{s+=$1}END{print s}')
  (cd "$ROOT" && tar -cf - -T "$LIST") | zstd -q -6 -T0 > "$F"
  Z=$(zstd -dc "$F" | shasum -a 256 | cut -c1-64); L2=$(cd "$ROOT" && tar -cf - -T "$LIST" | shasum -a 256 | cut -c1-64)
  [ "$Z" = "$L2" ] || { echo "VERIFY FAIL $ROOT"; rm -f "$F"; exit 1; }
  (cd "$ROOT" && tr '\n' '\0' < "$LIST" | xargs -0 rm -f); mv "$LIST" "$F.list"
  printf '\n%s: %s transcripts older than %s days packed to `%s` (then mac-mini `%s`), sha256 of the tar %s, file list beside it. Restore one: `zstd -dc ARCHIVE | tar -C %s -xf - ./path/file.jsonl`\n' \
    "$(date +%F)" "$N" "$DAYS" "$F" "$V/$(basename "$F")" "$Z" "$ROOT" >> "$ROOT/OLD-TRANSCRIPTS-IN-VAULT.md"
  receipt "packed $N old transcripts for the mini vault drive (tar.zst, outbox)" "$ROOT/**/*.jsonl (mtime +$DAYS)" $((K*1024)) "$F" "$WHY" "sha256 of tar stream equal to decompressed archive and second read: $Z"
  echo "PACKED $N files $((K/1024)) MiB -> $(du -m "$F" | cut -f1) MiB; free $(free_)";;
ship)  # ship [N]: N archives at once (default 4); one ssh stream to the mini tops out near 360 KiB/s (300 ms RTT)
  ls "$OUTBOX"/*.tar.zst 2>/dev/null | xargs -n1 -P "${2:-4}" "$0" ship-one;;
ship-one)
  F="$2"; B=$(basename "$F"); mkdir "$F.lock" 2>/dev/null || exit 0  # another shipper has it
  trap 'rmdir "$F.lock" 2>/dev/null' EXIT
  [ -e "$F" ] || exit 0; L=$(shasum -a 256 "$F" | cut -c1-64)
  ssh -o BatchMode=yes -o ServerAliveInterval=30 mac-mini-herdr "mkdir -p $V && cat > '$V/$B.part'" < "$F" || { echo "COPY FAIL $B"; exit 0; }
  M=$(ssh -o BatchMode=yes mac-mini-herdr "shasum -a 256 '$V/$B.part' | cut -c1-64")
  [ "$L" = "$M" ] || { echo "VERIFY FAIL $B"; exit 0; }
  ssh -o BatchMode=yes mac-mini-herdr "mv '$V/$B.part' '$V/$B'" || exit 0
  [ -e "$F.list" ] && scp -q "$F.list" "mac-mini-herdr:$V/$B.list" && rm -f "$F.list"
  S=$(stat -f %z "$F"); rm -f "$F"
  receipt "shipped packed archive to the mini vault drive" "$F" "$S" "mac-mini:$V/$B" "outbox drained" "sha256 of archive equal on both sides: $L"
  echo "SHIPPED $B";;
*) sed -n '2,15p' "$0"; exit 2;;
esac
