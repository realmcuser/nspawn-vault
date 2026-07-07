#!/bin/bash
# GFS-retention för ett ZFS-dataset.
# Usage: gfs-prune.sh <dataset> [hourly] [daily] [weekly] [monthly] [yearly]
set -euo pipefail
DATASET="$1"
GH="${2:-24}"    # en per timme, sista 24h
GD="${3:-7}"     # en per dag,  sista 7 dagar
GW="${4:-4}"     # en per vecka, sista 4 veckor
GM="${5:-12}"    # en per månad, sista 12 månader
GY="${6:-3}"     # en per år,   sista 3 år

echo "=== GFS prune: $DATASET (H${GH}/D${GD}/W${GW}/M${GM}/Y${GY}) ===" >&2

to_delete=$(zfs list -H -o name -t snapshot -s creation "$DATASET" \
    | sed "s|^${DATASET}@||" \
    | python3 /usr/libexec/nspawn-vault/gfs.py "$GH" "$GD" "$GW" "$GM" "$GY")

if [ -z "$to_delete" ]; then
    echo "Inget att rensa" >&2
    exit 0
fi

count=0
while IFS= read -r snap; do
    [ -z "$snap" ] && continue
    echo "Raderar: ${DATASET}@${snap}" >&2
    zfs destroy "${DATASET}@${snap}"
    count=$((count+1))
done <<< "$to_delete"

echo "Rensade $count snapshots" >&2
