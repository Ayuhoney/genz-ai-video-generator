#!/bin/sh
set -e
# Named volume mounts often arrive as root:root; app runs as uid 10001.
if [ -d /data/media ]; then
  chown -R app:app /data/media
  chmod u+rwX /data/media
fi
exec setpriv --reuid=10001 --regid=10001 --clear-groups -- "$@"
