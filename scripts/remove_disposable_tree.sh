#!/bin/sh
set -eu

target=${1:-}
if [ -z "$target" ]; then
  printf '%s\n' 'remove_disposable_tree.sh requires one target path' >&2
  exit 2
fi
case "$target" in
  /|.|..)
    printf '%s\n' "remove_disposable_tree.sh refuses dangerous target: $target" >&2
    exit 2
    ;;
esac

if [ ! -e "$target" ] && [ ! -L "$target" ]; then
  exit 0
fi

# A symlink root is an unlinkable leaf. Never chmod through it into a cache,
# sealed supply, another checkout, or any other foreign target.
if [ -L "$target" ]; then
  rm -f -- "$target"
  exit 0
fi

if [ -d "$target" ]; then
  # Sealed/materialized trees can legitimately contain 0555 directories. Restore
  # owner write authority only to directory nodes in the disposable destination;
  # find does not follow nested symlinks by default.
  find "$target" -type d -exec chmod u+w {} +
  rm -rf -- "$target"
  exit 0
fi

# Callers use this helper for disposable paths. A non-directory leaf can be
# removed directly; write permission on the leaf is not required for unlink.
rm -f -- "$target"
