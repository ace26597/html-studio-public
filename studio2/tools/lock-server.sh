#!/usr/bin/env bash
# Write the two lists of exact Python packages that install.sh installs for the server:
#
#   server-requirements.txt       pyproject.toml's dependencies and the `look` extra
#   server-dev-requirements.txt   the same and the `dev` extra (what the tests need)
#
# Each is resolved for every platform at once (uv's universal mode, Python 3.12), so the one file
# serves macOS and Linux on x86-64 and ARM64, and every package carries its hashes.
#
#   tools/lock-server.sh                 keep the versions already in the files; add only what changed
#   tools/lock-server.sh --upgrade       move every package to the newest release pyproject.toml allows
#   tools/lock-server.sh -c <file>       take the versions from a list such as `uv pip freeze` of a
#                                        tested environment (lines starting with -e are ignored)
#
# After a change, install from the new files into a fresh environment and run the tests before
# committing them: these files, not pyproject.toml, decide what a new install gets.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."                    # studio2

UPGRADE=(); CONSTRAINTS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --upgrade) UPGRADE=(--upgrade) ;;
    -c) grep -v '^-e ' "$2" >"${TMPDIR:-/tmp}/studio-lock-constraints.txt"
        CONSTRAINTS=(-c "${TMPDIR:-/tmp}/studio-lock-constraints.txt"); shift ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

lock() {                    # lock FILE EXTRA...
  local file="$1"; shift
  local extras=() extra
  for extra in "$@"; do extras+=(--extra "$extra"); done
  uv pip compile pyproject.toml "${extras[@]}" --universal --generate-hashes --python-version 3.12 \
    --no-header --no-annotate --quiet ${UPGRADE[@]+"${UPGRADE[@]}"} ${CONSTRAINTS[@]+"${CONSTRAINTS[@]}"} -o "$file"
  { printf '%s\n' \
      "# The exact packages install.sh installs for the server (pyproject.toml, extras: $*), every one" \
      "# checked against these hashes. Written by tools/lock-server.sh; change it with that, not by hand." \
      "#"
    grep -v '^#' "$file"
  } >"$file.new"
  mv "$file.new" "$file"
  printf '%s: %s packages\n' "$file" "$(grep -c '^[a-z0-9]' "$file")"
}

lock server-requirements.txt look
lock server-dev-requirements.txt look dev
