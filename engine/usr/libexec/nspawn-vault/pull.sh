#!/bin/bash
set -euo pipefail
HOST="$1"
NAME="$2"
KEY="${3:-/root/.ssh/nspawn-vault}"
POOL="${NSPAWN_VAULT_POOL:-vault}"
DATASET="${4:-${POOL}/${HOST%%.*}/$NAME}"

MNT=$(zfs get -H -o value mountpoint "$DATASET" 2>/dev/null) || {
    echo "Creating dataset $DATASET" >&2
    zfs create -p "$DATASET"
    MNT=$(zfs get -H -o value mountpoint "$DATASET")
}

SSH=(ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

# Default excludes - paths that never need restoring and just inflate every
# pull (especially the first, which has nothing to diff against). The
# --include for the DB dump must come before the /var/tmp/* exclude - rsync
# takes the first matching rule, not the most specific one.
RSYNC_EXCLUDES=(
    --exclude=/dev/*
    --exclude=/proc/*
    --exclude=/sys/*
    --exclude=/run/*
    --exclude=/tmp/*
    --exclude=/var/cache/*
    --include=/var/tmp/cockpit-nspawn-db.sql
    --exclude=/var/tmp/*
    # root's own cache dirs, not covered by /var/cache - confirmed live
    # (webapp1 @ source1.example.com, 2026-07-06) to be ~6GB of a
    # 13GB container: uv/npm package caches and headless-browser downloads
    # (camoufox, ms-playwright), all fully regenerable, never worth
    # restoring. Only covers /root - a container with real per-user home
    # dirs under /home/*/.cache would need its own exclude added here too.
    --exclude=/root/.cache/*
    --exclude=/root/.npm/*
)

STATE_DIR=/var/lib/nspawn-vault/state
mkdir -p "$STATE_DIR"
STATE="$STATE_DIR/${DATASET//\//_}.json"

fail() {
    printf '{"result":"failed","ts":"%s","msg":"%s"}\n' "$(date -Iseconds)" "$1" > "$STATE"
    echo "FAILED: $1" >&2
    exit 1
}

echo "=== Pull $NAME from $HOST ===" >&2

# 1) DB-snapshot om konfigurerad (no-op om ingen .conf finns).
#    Om STOP_DURING_BACKUP=true stoppas containern - garanterat
#    återstartad via trap nedan, oavsett hur resten av pull.sh går.
"${SSH[@]}" "$HOST" "snapshot-db $NAME" || fail "snapshot-db failed"
trap '"${SSH[@]}" "$HOST" "restore-after-backup $NAME" || echo "VARNING: restore-after-backup misslyckades" >&2' EXIT

# 2) rsync pull (read-only på källan via rrsync -ro)
rsync -aH --delete --numeric-ids \
    "${RSYNC_EXCLUDES[@]}" \
    -e "${SSH[*]}" \
    "$HOST:/$NAME/" "$MNT/" \
    || fail "rsync pull failed"

# 3) Atomär ZFS-snapshot
SNAP="${DATASET}@$(date +%Y%m%d-%H%M%S)"
zfs snapshot "$SNAP" || fail "zfs snapshot failed"
echo "Snapshot: $SNAP" >&2

printf '{"result":"success","ts":"%s","snap":"%s"}\n' "$(date -Iseconds)" "$SNAP" > "$STATE"
echo "=== Done ===" >&2
