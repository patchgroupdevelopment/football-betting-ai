#!/usr/bin/env bash
# GitHub delays scheduled workflows on busy days (hours, observed: 04:00 UTC run started at 10:55).
# A manually dispatched run (workflow_dispatch) does not wait in that queue, so:
#
#   watchdog.sh dispatch   (bot workflow) — if a daily slot has passed and no daily run started since,
#                          start one now
#   watchdog.sh guard      (daily workflow, scheduled event) — if another run already covered the
#                          current slot, print skip=true to $GITHUB_OUTPUT so this late run does nothing
#
# Daily slots (UTC) must match the cron entries in .github/workflows/daily.yml.
set -euo pipefail

SLOTS=("04:00" "11:00")
GRACE=600  # seconds after a slot before the watchdog steps in
REPO="${GITHUB_REPOSITORY:?}"

now=$(date -u +%s)
today=$(date -u +%Y-%m-%d)

# Latest slot that has started (optionally only after the grace period).
latest_slot() {
  local grace="$1" found=""
  for hhmm in "${SLOTS[@]}"; do
    local slot
    slot=$(date -u -d "$today $hhmm" +%s)
    if [ "$now" -ge $((slot + grace)) ]; then found="$slot"; fi
  done
  echo "$found"
}

# Daily runs created since the slot that succeeded or are still going (failed ones do not count).
runs_since() {
  local since="$1" exclude="${2:-0}"
  gh run list --repo "$REPO" --workflow daily.yml --limit 30 \
    --json databaseId,createdAt,status,conclusion \
    --jq "[.[] | select(.createdAt >= \"$since\" and .databaseId != $exclude
            and (.status != \"completed\" or .conclusion == \"success\"))] | length"
}

case "${1:-}" in
  dispatch)
    slot=$(latest_slot "$GRACE")
    if [ -z "$slot" ]; then echo "Bu gün gündəlik işin vaxtı hələ çatmayıb"; exit 0; fi
    since=$(date -u -d "@$slot" +%Y-%m-%dT%H:%M:%SZ)
    if [ "$(runs_since "$since")" -eq 0 ]; then
      echo "Gündəlik iş ($since) hələ başlamayıb — indi başladılır"
      gh workflow run daily.yml --repo "$REPO" --ref main
    else
      echo "Gündəlik iş ($since) artıq başlayıb"
    fi
    ;;
  guard)
    slot=$(latest_slot 0)
    skip=false
    if [ -n "$slot" ]; then
      since=$(date -u -d "@$slot" +%Y-%m-%dT%H:%M:%SZ)
      if [ "$(runs_since "$since" "${GITHUB_RUN_ID:-0}")" -gt 0 ]; then
        echo "Bu vaxt ($since) üçün iş artıq gedib — gecikmiş planlı iş buraxılır"
        skip=true
      fi
    fi
    echo "skip=$skip" >> "${GITHUB_OUTPUT:-/dev/null}"
    ;;
  *)
    echo "istifadə: watchdog.sh dispatch|guard" >&2
    exit 2
    ;;
esac
