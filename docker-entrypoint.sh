#!/bin/sh
# Drop privileges to appuser before running the CMD.
#
# We start the container as root so a freshly-mounted persistent volume at
# /app/data (initially root-owned by the platform) can be chowned to appuser.
# The chown is idempotent: a no-op on warm starts where the directory is
# already correctly owned.
set -e
chown appuser:appuser /app/data
exec gosu appuser "$@"
