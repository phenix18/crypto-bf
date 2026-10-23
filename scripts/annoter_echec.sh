#!/usr/bin/env bash
# Surface the useful lines of a failed build log as GitHub annotations.
#
# Why this exists: a workflow run's raw logs are served from a host that not
# every client can reach, and "Process completed with exit code 1" is not a
# diagnosis. Annotations come back from the API — but GitHub keeps only about
# ten error annotations per step, so the selection has to be deliberate rather
# than a blind tail.
#
#   usage: scripts/annoter_echec.sh build.log
set -u
journal="${1:?usage: annoter_echec.sh <fichier-journal>}"
[ -f "$journal" ] || { echo "::error::journal introuvable : $journal"; exit 0; }

{
  # Gradle says what it refused right under this heading.
  sed -n '/What went wrong/,+6p' "$journal"
  # Compiler and toolchain errors, wherever they sit in the log. The Android
  # SDK prints a licence line per package it checks; those matched "license"
  # and ate the whole annotation budget on the first run that needed it.
  grep -aiE "error:|caused by:|could not |not found|sdk location|licen[cs]e" "$journal" \
    | grep -aivE "licen[cs]e for package|checking the licen[cs]e|licen[cs]e .* accepted" \
    | head -n 5
  # And the last word, which is usually the task that gave up.
  tail -n 3 "$journal"
} | sed 's/[[:space:]]\{2,\}/ /g; s/^ //; s/ $//' \
  | grep -v '^$' | awk '!vu[$0]++' | head -n 10 | cut -c1-360 \
  | while IFS= read -r ligne; do echo "::error::$ligne"; done
