#!/bin/bash
# Engångsverktyg: skapar ZFS-poolen som nspawn-vault använder.
# Körs manuellt, ALDRIG automatiskt - skapar en pool är destruktivt
# för vad som än ligger på enheten sedan innan.
#
# Usage: init-pool.sh /dev/vdb [poolnamn]
set -euo pipefail
DEVICE="${1:?Usage: init-pool.sh <device> [poolname]}"
POOL="${2:-vault}"

if zpool list "$POOL" >/dev/null 2>&1; then
    echo "Poolen $POOL finns redan:" >&2
    zpool status "$POOL"
    exit 0
fi

echo "Skapar zpool '$POOL' på $DEVICE ..." >&2
read -r -p "Detta RADERAR allt på $DEVICE. Fortsätt? (skriv 'ja'): " confirm
[ "$confirm" = "ja" ] || { echo "Avbrutet." >&2; exit 1; }

zpool create "$POOL" "$DEVICE"
zpool status "$POOL"
echo "Klart. Pool '$POOL' redo att användas." >&2
