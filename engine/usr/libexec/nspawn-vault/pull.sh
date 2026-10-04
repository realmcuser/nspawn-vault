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
    # $1 is JSON-escaped here rather than left raw - every caller used to
    # pass a static string with no quotes/newlines in it, so this was never
    # hit, but run_remote() below now passes real captured command output,
    # which can easily contain either and would otherwise produce invalid
    # JSON in the state file.
    local msg
    msg=$(printf '%s' "$1" | tr '\n' ' ' | sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '{"result":"failed","ts":"%s","msg":"%s"}\n' "$(date -Iseconds)" "$msg" > "$STATE"
    echo "FAILED: $1" >&2
    exit 1
}

# Runs one dispatch.sh-whitelisted command on the source host and, on
# failure, folds its actual captured output into fail()'s msg instead of a
# generic canned string - so a failed DB dump/pre-snapshot hook shows the
# real reason (e.g. "mysqldump failed - is MariaDB running...") directly in
# the web UI's per-container line, without needing to open the pull log.
run_remote() {
    local step="$1" cmd="$2" out
    if ! out=$("${SSH[@]}" "$HOST" "$cmd" 2>&1); then
        local last_line="${out##*$'\n'}"
        fail "$step: ${last_line:-no output (SSH/network failure?)}"
    fi
}

# Same as run_remote(), but treats a source host whose dispatch.sh doesn't
# recognize this command yet (the exact rejection dispatch.sh's own default
# case prints) as "not upgraded yet", not a failure - logs a warning and
# lets the pull continue. Needed because pre-snapshot is a new command
# (nspawn-vault 0.1.0-16): across a fleet, the vault's own package upgrades
# independently of each source host's source-host/dispatch.sh being
# manually re-installed there (see CLAUDE.md's source-host/ note) - without
# this, every pull for every not-yet-upgraded source host would hard-fail
# the moment the vault side alone is upgraded. Confirmed live: this exact
# scenario broke every pull for fhdcore-jf.vpn.fhd.se the moment pull.sh
# was updated ahead of its dispatch.sh, 2026-08-03.
run_remote_optional() {
    local step="$1" cmd="$2" out
    if ! out=$("${SSH[@]}" "$HOST" "$cmd" 2>&1); then
        if [[ "$out" == *"dispatch.sh: rejected command"* ]]; then
            echo "VARNING: $HOST känner inte igen '$cmd' än (source-host/dispatch.sh behöver uppdateras där) - hoppar över detta steg för denna pull" >&2
        else
            local last_line="${out##*$'\n'}"
            fail "$step: ${last_line:-no output (SSH/network failure?)}"
        fi
    fi
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

# 1) Valfri generisk pre-snapshot-hook (no-op om ingen .hook finns), sedan
#    DB-snapshot om konfigurerad (no-op om ingen .cnf finns) - båda
#    oberoende av varandra, båda blockerar pullen lika hårt vid fel.
#    Om STOP_DURING_BACKUP=true stoppas containern - garanterat
#    återstartad via trap nedan, oavsett hur resten av pull.sh går.
run_remote_optional "pre-snapshot" "pre-snapshot $NAME"
trap '"${SSH[@]}" "$HOST" "restore-after-backup $NAME" || echo "VARNING: restore-after-backup misslyckades" >&2' EXIT
run_remote "snapshot-db" "snapshot-db $NAME"

# 2) rsync pull (read-only på källan via rrsync -ro)
# Plain -a (-rlptgoD) drops extended attributes, including Linux file
# capabilities (security.capability, what `setcap` sets) - a setcap'd
# binary silently loses that capability on every pull. -X would fix that
# but was tried and reverted live on 2026-10-04: with SELinux Enforcing on
# the vault, -X also tries to sync security.selinux, which this process
# (SELinux domain unconfined_service_t) cannot write without
# CAP_MAC_ADMIN - broke EVERY pull with "lremovexattr(...): Permission
# denied" within minutes of deploying, confirmed reproducible in testing
# (filtering security.selinux out of the xattr set does NOT avoid this -
# rsync's own xattr-comparison still trips the same permission check).
# Capabilities are instead restored separately, below (step 2b), via
# list-capabilities/setcap - narrower, and confirmed live to need no
# SELinux privilege at all.
rsync_rc=0
rsync -aH --delete --numeric-ids \
    "${RSYNC_EXCLUDES[@]}" \
    -e "${SSH[*]}" \
    "$HOST:/$NAME/" "$MNT/" || rsync_rc=$?
# Exit 24 ("partial transfer due to vanished source files") means rsync
# raced against the container deleting/replacing one of its own files
# mid-sync - normal on an actively-running container (seen live on
# hermes-agent: .hermes/cache/scratch/*.sh, kanban.db-shm/-wal), not a real
# backup failure. Treating it as fatal made check-stale.sh's dead-man's
# switch fire immediately on a benign race that the very next pull resolves
# on its own.
if [ "$rsync_rc" -ne 0 ] && [ "$rsync_rc" -ne 24 ]; then
    fail "rsync pull failed (exit $rsync_rc)"
elif [ "$rsync_rc" -eq 24 ]; then
    echo "VARNING: rsync avslutade med kod 24 (filer försvann under överföringen - normalt på en aktiv container) - fortsätter pullen" >&2
fi

# 2b) Återställ Linux file capabilities (setcap) som rsync -a ovan tappade -
# se kommentaren på rsync-anropet för varför -X inte används. Best-effort:
# ingen output alls (container utan capability-filer) och en källhost som
# inte känner igen kommandot än (gammal dispatch.sh) är båda normala fall,
# inte ett pull-fel - skiljer sig därför från run_remote/run_remote_optional
# genom att faktiskt behöva den riktiga stdout-utdatan, inte bara
# fel/success.
caps_out=$("${SSH[@]}" "$HOST" "list-capabilities $NAME" 2>&1) || caps_out=""
if [[ "$caps_out" == *"dispatch.sh: rejected command"* ]]; then
    echo "VARNING: $HOST känner inte igen 'list-capabilities' än (source-host/dispatch.sh behöver uppdateras där) - hoppar över capability-återställning för denna pull" >&2
    caps_out=""
fi
caps_applied=0
while IFS=' ' read -r cap_path cap_value; do
    [ -n "$cap_path" ] && [ -n "$cap_value" ] || continue
    # cap_path kommer från containerns egen sökvägsrymd (t.ex. /usr/bin/foo)
    # - lös den mot $MNT och vägra följa den utanför, samma försiktighet som
    # web/backend/vault_archive.py:s resolve_safe_path tillämpar på
    # motsvarande problem för filbläddraren.
    resolved=$(realpath -m "$MNT$cap_path" 2>/dev/null) || continue
    case "$resolved" in
        "$MNT"/*) ;;
        *) echo "VARNING: ignorerar capability-sökväg utanför containerns rot: $cap_path" >&2; continue ;;
    esac
    [ -f "$resolved" ] || continue
    if setcap "$cap_value" "$resolved" 2>/dev/null; then
        caps_applied=$((caps_applied + 1))
    else
        echo "VARNING: kunde inte sätta capability '$cap_value' på $cap_path" >&2
    fi
done <<< "$caps_out"
[ "$caps_applied" -gt 0 ] && echo "Återställde $caps_applied capability-fil(er)" >&2

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
DNF_UPDATE_DETECTED=false
if [ "$THRESHOLD" -gt 0 ]; then
    SNAP_COUNT=$(zfs list -H -o name -t snapshot "$DATASET" 2>/dev/null | wc -l)
    if [ "$SNAP_COUNT" -gt "$GRACE_PULLS" ]; then
        PREV_SNAP=$(zfs list -H -o name -t snapshot -s creation "$DATASET" 2>/dev/null | tail -2 | head -1)
        CHANGED=$(zfs diff -H "$PREV_SNAP" "$SNAP" 2>/dev/null | wc -l) || CHANGED=0
        if [ "$CHANGED" -ge "$THRESHOLD" ]; then
            # Ett riktigt dnf/dnf-automatic-paketuppdatering kan i sig
            # ändra tusentals filer helt legitimt (sett live på
            # fhdcore-jf2, ~12000 filer). /var/log/dnf.log är en vanlig
            # fil i det som redan rsyncats hem, och rsync -a bevarar dess
            # ursprungliga mtime - så att jämföra den mot PREV_SNAP:s egen
            # skapelsetid (zfs har redan detta, "get -p" ger rått
            # epoksekunder) är konkret bevis, inte en gissning. Fångar
            # även en människa som kör "dnf upgrade" manuellt.
            PREV_SNAP_EPOCH=$(zfs get -H -o value -p creation "$PREV_SNAP" 2>/dev/null || echo 0)
            for dnf_log in "$MNT/var/log/dnf.log" "$MNT/var/log/dnf.rpm.log"; do
                [ -f "$dnf_log" ] || continue
                log_epoch=$(stat -c %Y "$dnf_log" 2>/dev/null || echo 0)
                if [ "$log_epoch" -gt "$PREV_SNAP_EPOCH" ]; then
                    DNF_UPDATE_DETECTED=true
                    break
                fi
            done

            if [ "$DNF_UPDATE_DETECTED" = true ]; then
                echo "INFO: $CHANGED ändrade poster, men dnf-loggen visar en paketuppdatering sedan föregående snapshot - flaggar inte som ransomware" >&2
            else
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
fi

printf '{"result":"success","ts":"%s","snap":"%s","changed_entries":%s,"ransomware_suspected":%s,"dnf_update_detected":%s}\n' \
    "$(date -Iseconds)" "$SNAP" "$CHANGED" "$SUSPECTED" "$DNF_UPDATE_DETECTED" > "$STATE"
echo "=== Done ===" >&2
