#!/bin/bash
# Pull alla containers för en källserver.
# Anropas av systemd: pull-host.sh <host>
set -euo pipefail
HOST="$1"
KEY=/root/.ssh/nspawn-vault
CONF="/etc/nspawn-vault/${HOST}/containers"

if [ ! -f "$CONF" ]; then
    echo "Ingen containerlista för $HOST: $CONF" >&2
    exit 1
fi

ERRORS=0
while IFS= read -r name || [ -n "$name" ]; do
    [[ "$name" =~ ^#|^$ ]] && continue
    echo "--- $name ---" >&2
    bash /usr/libexec/nspawn-vault/pull.sh "$HOST" "$name" "$KEY" </dev/null || ERRORS=$((ERRORS+1))
done < "$CONF"

[ "$ERRORS" -gt 0 ] && exit 1
exit 0
