#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
env_file="$project_dir/.env"

if [[ ! -f "$env_file" ]]; then
  echo "error: $env_file does not exist; copy .env.example to .env and configure it" >&2
  exit 1
fi

# .env is a local, trusted shell configuration file. Export its values so they
# are also available to commands invoked by this script.
set -a
# shellcheck source=/dev/null
source "$env_file"
set +a

if [[ -z "${UPSTREAM_REPO_DIR:-}" ]]; then
  echo "error: UPSTREAM_REPO_DIR is not set in $env_file" >&2
  exit 1
fi

# Resolve relative paths from the project root, independent of the caller's
# current working directory.
if [[ "$UPSTREAM_REPO_DIR" = /* ]]; then
  upstream_dir="$UPSTREAM_REPO_DIR"
else
  upstream_dir="$project_dir/$UPSTREAM_REPO_DIR"
fi

if ! upstream_dir="$(cd -- "$upstream_dir" 2>/dev/null && pwd)"; then
  echo "error: upstream repository directory does not exist: $UPSTREAM_REPO_DIR" >&2
  exit 1
fi

if [[ "$upstream_dir" == "$project_dir" ]]; then
  echo "error: UPSTREAM_REPO_DIR must point to the original repository clone" >&2
  exit 1
fi

if ! git -C "$upstream_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: not a Git working tree: $upstream_dir" >&2
  exit 1
fi

upstream_conferences="$upstream_dir/_data/conferences.yml"
current_conferences="$project_dir/_data/conferences.yml"

if [[ ! -f "$upstream_conferences" ]]; then
  echo "error: upstream conference file does not exist: $upstream_conferences" >&2
  exit 1
fi

echo "Pulling upstream repository in $upstream_dir"
cd -- "$upstream_dir"
git pull --ff-only

echo "Updating $current_conferences"
cd -- "$project_dir"
python3 "$script_dir/update_conferences.py" \
  "$current_conferences" \
  "$upstream_conferences" \
  --output "$current_conferences"

echo "Conference data updated successfully"
