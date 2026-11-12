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
    # En "extra path" (se pull-extra.sh och pull-backup-threat-model.md,
    # "Bind-mounted external storage") listas i samma containers-fil, med
    # samma namn/UI-behandling som en riktig container, men har sin egen
    # dedikerade nyckel under <host>/extra/<name>.key istället för en riktig
    # nspawn-container - dess förekomst är det som avgör vilket skript som
    # kör den här posten, inte något i containers-filen självt.
    EXTRA_KEY="/etc/nspawn-vault/${HOST}/extra/${name}.key"
    if [ -f "$EXTRA_KEY" ]; then
        bash /usr/libexec/nspawn-vault/pull-extra.sh "$HOST" "$name" "$EXTRA_KEY" </dev/null || ERRORS=$((ERRORS+1))
    else
        bash /usr/libexec/nspawn-vault/pull.sh "$HOST" "$name" "$KEY" </dev/null || ERRORS=$((ERRORS+1))
    fi
done < "$CONF"

[ "$ERRORS" -gt 0 ] && exit 1
exit 0
