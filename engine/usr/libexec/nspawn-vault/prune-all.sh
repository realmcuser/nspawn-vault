#!/bin/bash
# Kör GFS-retention på alla datasets under <pool>/<host>/<container>.
# Läser GFS-parametrar från /etc/nspawn-vault/gfs.conf om den finns.
set -euo pipefail
POOL="${NSPAWN_VAULT_POOL:-vault}"
GFS_CONF=/etc/nspawn-vault/gfs.conf
GH=24; GD=7; GW=4; GM=12; GY=3
[ -f "$GFS_CONF" ] && source "$GFS_CONF"

ERRORS=0
for dataset in $(zfs list -H -o name -t filesystem -r "$POOL" | grep -E "${POOL}/[^/]+/[^/]+$"); do
    bash /usr/libexec/nspawn-vault/gfs-prune.sh "$dataset" "$GH" "$GD" "$GW" "$GM" "$GY" \
        || ERRORS=$((ERRORS+1))
done

[ "$ERRORS" -gt 0 ] && exit 1
exit 0
