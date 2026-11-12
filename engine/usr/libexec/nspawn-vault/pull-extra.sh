#!/bin/bash
# Pulls an "extra path" - a host-level directory on the source host that is
# NOT inside any container's own rootfs tree, typically data that's instead
# Bind=-mounted *into* one or more containers via their .nspawn unit (see
# pull-backup-threat-model.md's "Bind-mounted external storage" section).
# pull.sh's normal rsync only ever reads $CONTAINERS_ROOT/<name>/ on the
# source host - a systemd-nspawn Bind= mount is only visible inside the
# container's own running mount namespace, never at that path from the
# host's root namespace, so anything living under a Bind= target is
# silently invisible to a normal pull. No error, no warning - confirmed
# live on vilhelmina-csrv.vpn.fhd.se 2026-11-12 (/usr/butik/samba, backed
# by /srv/fhd-shares/fhdcore-vilhelmina, came back as an empty directory on
# every pull for weeks with the dashboard reporting success throughout).
#
# Unlike pull.sh, this does NOT go through dispatch.sh. Each extra path has
# its own dedicated SSH key whose authorized_keys forced command is a bare
# `rrsync -ro <exact-path>` (see source-host/README.md's "Extra paths"
# section) - a forced command always runs instead of whatever the client
# asked for, so this script must never send a remote *command* over that
# key (no pre-snapshot, no snapshot-db, no list-capabilities - those would
# just be silently ignored and the rrsync line would run in their place,
# confusing the rsync/ssh protocol handshake). Only the bare `rsync -e ssh`
# invocation below is safe to use with this key.
set -euo pipefail
HOST="$1"
LABEL="$2"
KEY="${3:-/etc/nspawn-vault/${HOST}/extra/${LABEL}.key}"
POOL="${NSPAWN_VAULT_POOL:-vault}"
DATASET="${4:-${POOL}/${HOST%%.*}/$LABEL}"

[ -f "$KEY" ] || { echo "Ingen nyckel för extra path $HOST/$LABEL: $KEY" >&2; exit 1; }

MNT=$(zfs get -H -o value mountpoint "$DATASET" 2>/dev/null) || {
    echo "Creating dataset $DATASET" >&2
    zfs create -p "$DATASET"
    MNT=$(zfs get -H -o value mountpoint "$DATASET")
}

SSH=(ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

STATE_DIR=/var/lib/nspawn-vault/state
mkdir -p "$STATE_DIR"
STATE="$STATE_DIR/${DATASET//\//_}.json"

fail() {
    # See pull.sh's matching comment - python3's json.dumps covers every
    # JSON-mandated control-character escape, not just backslash/quote.
    local msg
    msg=$(printf '%s' "$1" | tr '\n' ' ')
    msg=$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$msg")
    printf '{"result":"failed","ts":"%s","msg":%s}\n' "$(date -Iseconds)" "$msg" > "$STATE"
    echo "FAILED: $1" >&2
    exit 1
}

# Samma paus-mekanism som pull.sh (steg 4 nedan skriver markören) - låter en
# bekräftad misstänkt-ransomware-incident stoppa framtida pullar av den här
# extra-sökvägen tills någon kvitterat i UI:et, precis som för en container.
PAUSE_MARKER="$STATE_DIR/paused/${HOST}_${LABEL}"
if [ -f "$PAUSE_MARKER" ]; then
    echo "PAUSED: $HOST/$LABEL väntar på bekräftelse i UI:et, hoppar över pull" >&2
    exit 0
fi

echo "=== Pull extra path $LABEL from $HOST ===" >&2

# rsync pull. Inget fjärrkommando skickas - nyckelns forced command
# (rrsync -ro <exakt sökväg>) avgör precis vad som serveras, oavsett vad vi
# skriver här. "$HOST:" utan sökväg efter kolonet ber om exakt det rrsync är
# inställd på, vilket är hela poängen med en nyckel per extra-sökväg.
rsync_rc=0
rsync -a --delete --numeric-ids \
    -e "${SSH[*]}" \
    "$HOST:" "$MNT/" || rsync_rc=$?
if [ "$rsync_rc" -ne 0 ] && [ "$rsync_rc" -ne 24 ]; then
    fail "rsync pull failed (exit $rsync_rc)"
elif [ "$rsync_rc" -eq 24 ]; then
    echo "VARNING: rsync avslutade med kod 24 (filer försvann under överföringen) - fortsätter" >&2
fi

SNAP="${DATASET}@$(date +%Y%m%d-%H%M%S)"
zfs snapshot "$SNAP" || fail "zfs snapshot failed"
echo "Snapshot: $SNAP" >&2

# Samma ransomware-heuristik som pull.sh (steg 4 där) - återanvänder samma
# per-namn-tröskelfilskonvention, LABEL spelar containernamnets roll.
NOTIFY_CONF=/etc/nspawn-vault/notify.conf
[ -f "$NOTIFY_CONF" ] && source "$NOTIFY_CONF"
THRESHOLD="${RANSOMWARE_DIFF_THRESHOLD:-500}"
case "$THRESHOLD" in ''|*[!0-9]*) THRESHOLD=500 ;; esac
PER_LABEL_THRESHOLD_FILE="/etc/nspawn-vault/${HOST}/${LABEL}.ransomware-threshold"
if [ -f "$PER_LABEL_THRESHOLD_FILE" ]; then
    PCT=$(tr -d '[:space:]' < "$PER_LABEL_THRESHOLD_FILE")
    case "$PCT" in ''|*[!0-9]*) : ;; *) THRESHOLD="$PCT" ;; esac
fi
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
            zfs hold nspawn-vault-ransomware "$PREV_SNAP" 2>/dev/null || true
            mkdir -p "$STATE_DIR/paused"
            printf '{"snap":"%s","dataset":"%s","detected_ts":"%s","changed_entries":%s}\n' \
                "${PREV_SNAP#*@}" "$DATASET" "$(date -Iseconds)" "$CHANGED" \
                > "$STATE_DIR/paused/${HOST}_${LABEL}"
        fi
    fi
fi

printf '{"result":"success","ts":"%s","snap":"%s","changed_entries":%s,"ransomware_suspected":%s,"dnf_update_detected":false}\n' \
    "$(date -Iseconds)" "$SNAP" "$CHANGED" "$SUSPECTED" > "$STATE"
echo "=== Done ===" >&2
