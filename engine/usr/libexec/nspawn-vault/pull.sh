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

# Auto-paus vid misstänkt ransomware (se steg 4 nedan): så länge denna
# markörfil finns hoppas pullen över helt, utan att röra state-JSON:en -
# vilket låter den åldras och trigga check-stale.sh:s vanliga stale-larm
# också, som en andra påminnelse om att någon behöver bekräfta i UI:et.
# Tas bort av web-backendens "acknowledge & resume"-endpoint.
PAUSE_MARKER="$STATE_DIR/paused/${HOST}_${NAME}"
if [ -f "$PAUSE_MARKER" ]; then
    echo "PAUSED: $HOST/$NAME väntar på bekräftelse i UI:et, hoppar över pull" >&2
    exit 0
fi

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

# 4) Ransomware-heuristik: räkna ändrade/tillagda/borttagna filer sedan
#    föregående snapshot via "zfs diff" - läser bara ZFS:s egna
#    ändringsmetadata (ingen filsystemsgenomgång), så det är billigt även
#    för stora containrar. check-stale.sh larmar direkt om CHANGED når
#    tröskeln (RANSOMWARE_DIFF_THRESHOLD i notify.conf, 0 = av).
NOTIFY_CONF=/etc/nspawn-vault/notify.conf
[ -f "$NOTIFY_CONF" ] && source "$NOTIFY_CONF"
THRESHOLD="${RANSOMWARE_DIFF_THRESHOLD:-500}"
case "$THRESHOLD" in ''|*[!0-9]*) THRESHOLD=500 ;; esac
# Grace period: hoppa över kontrollen för en containers första GRACE_PULLS
# pullar - en nyskapad container fylls ofta med filer under sina första
# körningar (initial datamigrering etc.), vilket annars trippar tröskeln
# på helt legitim grund. Default 3 = kontrollen börjar från 4:e pullen.
GRACE_PULLS="${RANSOMWARE_GRACE_PULLS:-3}"
case "$GRACE_PULLS" in ''|*[!0-9]*) GRACE_PULLS=3 ;; esac

CHANGED=0
SUSPECTED=false
if [ "$THRESHOLD" -gt 0 ]; then
    SNAP_COUNT=$(zfs list -H -o name -t snapshot "$DATASET" 2>/dev/null | wc -l)
    if [ "$SNAP_COUNT" -gt "$GRACE_PULLS" ]; then
        PREV_SNAP=$(zfs list -H -o name -t snapshot -s creation "$DATASET" 2>/dev/null | tail -2 | head -1)
        CHANGED=$(zfs diff -H "$PREV_SNAP" "$SNAP" 2>/dev/null | wc -l) || CHANGED=0
        if [ "$CHANGED" -ge "$THRESHOLD" ]; then
            SUSPECTED=true
            echo "VARNING: $CHANGED ändrade poster sedan föregående snapshot (tröskel: $THRESHOLD) - möjlig ransomware, se dashboarden" >&2
            # Skydda den senaste kända-goda snapshotten från gfs-prune.sh
            # oavsett GFS-retention/lagringstryck, tills någon bekräftat i
            # UI:et. Best-effort - ska aldrig få en i övrigt lyckad pull
            # att misslyckas.
            zfs hold nspawn-vault-ransomware "$PREV_SNAP" 2>/dev/null || true
            mkdir -p "$STATE_DIR/paused"
            printf '{"snap":"%s","dataset":"%s","detected_ts":"%s","changed_entries":%s}\n' \
                "${PREV_SNAP#*@}" "$DATASET" "$(date -Iseconds)" "$CHANGED" \
                > "$STATE_DIR/paused/${HOST}_${NAME}"
        fi
    fi
fi

printf '{"result":"success","ts":"%s","snap":"%s","changed_entries":%s,"ransomware_suspected":%s}\n' \
    "$(date -Iseconds)" "$SNAP" "$CHANGED" "$SUSPECTED" > "$STATE"
echo "=== Done ===" >&2
