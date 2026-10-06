#!/usr/bin/env bash
# Keeps the SQLite database between GitHub Actions runs, on the orphan branch "state".
#
#   state.sh pull          restore data/app.db from the branch (if it exists)
#   state.sh push          save data/app.db when it changed (always wins)
#   state.sh push --yield  save only if nobody else saved meanwhile (used by the bot poller,
#                          so it never overwrites the daily run's results)
#
# The branch always holds a single commit, so the repository does not grow with every run.
set -euo pipefail

DB="data/app.db"
REMOTE="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
mkdir -p data

hash_db() { sha256sum "$DB" | cut -d' ' -f1; }

case "${1:-}" in
  pull)
    sha="$(git ls-remote "$REMOTE" refs/heads/state | cut -f1)"
    if [ -n "$sha" ]; then
      git fetch -q --depth=1 "$REMOTE" state
      git show FETCH_HEAD:app.db > "$DB"
      echo "$sha" > data/.state_sha
      hash_db > data/.state_hash
      echo "Baza bərpa olundu (${sha:0:7}, $(du -h "$DB" | cut -f1))"
    else
      : > data/.state_sha
      : > data/.state_hash
      echo "state budağı hələ yoxdur — yeni baza yaradılacaq"
    fi
    ;;
  push)
    if [ ! -s "$DB" ]; then echo "Baza yoxdur — saxlanılmır"; exit 0; fi
    python -c "import sqlite3; c = sqlite3.connect('$DB'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
    if [ "$(hash_db)" = "$(cat data/.state_hash 2>/dev/null || true)" ]; then
      echo "Baza dəyişməyib — saxlamağa ehtiyac yoxdur"
      exit 0
    fi
    python -c "import sqlite3; c = sqlite3.connect('$DB'); c.execute('VACUUM'); c.close()"
    pulled_sha="$(cat data/.state_sha 2>/dev/null || true)"
    work="$(mktemp -d)"
    cp "$DB" "$work/app.db"
    cd "$work"
    git init -q
    git checkout -q -b state
    git add app.db
    git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
      commit -q -m "state: $(date -u +%Y-%m-%dT%H:%MZ)"
    if [ "${2:-}" = "--yield" ] && [ -n "$pulled_sha" ]; then
      if git push -q --force-with-lease="refs/heads/state:${pulled_sha}" "$REMOTE" state:state; then
        echo "Baza saxlanıldı"
      else
        echo "Başqa iş bazanı artıq yeniləyib — bu işin dəyişiklikləri saxlanılmadı"
      fi
    else
      git push -q -f "$REMOTE" state:state
      echo "Baza saxlanıldı"
    fi
    ;;
  *)
    echo "İstifadə: state.sh pull | push [--yield]" >&2
    exit 2
    ;;
esac
