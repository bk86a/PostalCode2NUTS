#!/bin/sh
# Drop privileges to appuser before running the CMD.
#
# When the container starts as root (the typical case — most platforms mount
# persistent volumes root-owned), this fixes ownership on /app/data so the
# unprivileged service can write the SQLite cache, then drops to appuser via
# gosu. The chown is idempotent: a no-op on warm starts where the directory
# is already correctly owned.
#
# When the container is launched with `--user appuser` (or any non-root UID)
# already, we skip the chown (we can't perform it without root) and just
# exec the CMD as the current user. Operators choosing this path are
# responsible for making sure /app/data is writable by that user.
set -e
if [ "$(id -u)" = "0" ]; then
    chown appuser:appuser /app/data
    exec gosu appuser "$@"
fi
exec "$@"
