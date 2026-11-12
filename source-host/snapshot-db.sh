#!/bin/bash
# snapshot-db.sh <container-name>
#
# Only ever invoked by dispatch.sh (the name is already validated there,
# but re-checked here defensively since this script could in principle be
# run directly). Best-effort application-consistent DB dump, taken *inside*
# the container via systemd-run so the DB password never has to leave this
# host (see ../pull-backup-threat-model.md section 3.3) - the vault never
# sees or sends any DB credential.
#
# Containers with no DB configured here are a normal, supported case - this
# just no-ops (exit 0) for them, it does not treat "no DB" as a failure.
#
# Not every container has a systemd of its own to connect to - see
# run_in_container() below and pre-snapshot.sh's matching comment for why
# this falls back to nsenter in that case, and why that's told apart from
# a real mysqldump failure by systemd-run's own error text rather than by
# probing the container's filesystem.
#
# Credentials file: /etc/cockpit-nspawn/pull/<name>.cnf, a mysql [client]
# section with the DB password, mode 600, created once by whoever sets up
# pull backups for this container (see example.cnf in this directory).
#
# The dump lands inside the container's own tree so it rides along with the
# rsync pull.sh triggers right after this; restore-after-backup.sh removes
# it again afterwards so a stale plaintext dump doesn't sit around on disk
# between pulls.
#
# Result is also recorded to /etc/cockpit-nspawn/backup-status/<name>.json
# (same shape push-mode's generated backup script writes) so BackupDialog
# can show it. This only reflects the dump step run here - not whether the
# vault's subsequent rsync of the container tree actually completed.

set -euo pipefail

NAME_RE='^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'
name="${1:-}"
[[ "$name" =~ $NAME_RE ]] || { echo "snapshot-db.sh: invalid container name: '$name'" >&2; exit 1; }

STATUS_DIR="/etc/cockpit-nspawn/backup-status"
# Separate filename from push-mode's own "${name}.json" - that one records
# a full backup run, this one only a DB dump step, keyed by the same
# container name; sharing a file would let one mode's status overwrite
# and be mislabeled as the other's.
STATUS_FILE="$STATUS_DIR/${name}-dbdump.json"

write_status() {
    mkdir -p "$STATUS_DIR"
    printf '{"result":"%s","timestamp":"%s","size_bytes":%s,"message":"%s","dump_path":"%s"}\n' \
        "$1" "$(date -Iseconds)" "${3:-0}" "$(printf '%s' "${2:-}" | tr '"' "'")" "${4:-}" > "$STATUS_FILE"
}

# See pre-snapshot.sh for the detailed rationale - identical mechanism,
# duplicated here rather than shared since these two scripts have no
# common sourced library today and dispatch.sh only ever execs one fixed,
# whitelisted script name per command.
# $? right after "if CMD; then ... fi" with no matching else is 0
# regardless of CMD's real exit status (that's the if-construct's own
# exit status, per POSIX - not CMD's) - rc is captured in an explicit
# else instead, where $? is still CMD's own real exit code.
HOOK_TIMEOUT_SEC="${HOOK_TIMEOUT_SEC:-600}"
run_in_container() {
    local cmd="$1" out rc pid
    if out=$(systemd-run --machine="$name" --pipe --wait --quiet -- bash -c "$cmd" 2>&1); then
        printf '%s' "$out"
        return 0
    else
        rc=$?
    fi
    if [[ "$out" != *"Failed to connect to bus"* ]]; then
        printf '%s' "$out"
        return "$rc"
    fi

    pid=$(machinectl show "$name" -p Leader --value 2>/dev/null) || true
    if [[ -z "$pid" ]]; then
        printf '%s' "no systemd in '$name' (systemd-run: $out) and could not determine its leader PID to fall back to nsenter"
        return 1
    fi
    if out=$(timeout "$HOOK_TIMEOUT_SEC" nsenter -t "$pid" -a bash -c "$cmd" 2>&1); then
        printf '%s' "$out"
        return 0
    else
        rc=$?
    fi
    printf '%s' "$out"
    return "$rc"
}

MYCNF_SRC="/etc/cockpit-nspawn/pull/${name}.cnf"
if [ ! -f "$MYCNF_SRC" ]; then
    echo "snapshot-db.sh: no DB configured for '$name' ($MYCNF_SRC missing) - skipping DB dump" >&2
    exit 0
fi

if ! machinectl show "$name" --property=State 2>/dev/null | grep -q '^State=running'; then
    write_status "failed" "container is not running - cannot dump its DB"
    echo "snapshot-db.sh: container '$name' is not running - cannot dump its DB" >&2
    exit 1
fi

# Paths as seen from inside the container's own namespace.
CREDS_IN_CONTAINER="/root/.np-mycnf"
DUMP_IN_CONTAINER="/var/tmp/cockpit-nspawn-db.sql"
# Same paths as seen from out here, via the container's on-disk tree.
dest_creds="/var/lib/machines/${name}${CREDS_IN_CONTAINER}"
dest_dump="/var/lib/machines/${name}${DUMP_IN_CONTAINER}"

install -m600 "$MYCNF_SRC" "$dest_creds"
trap 'rm -f "$dest_creds"' EXIT

if ! dump_out=$(run_in_container "mysqldump --defaults-extra-file=$CREDS_IN_CONTAINER --single-transaction --routines --events --all-databases > $DUMP_IN_CONTAINER"); then
    write_status "failed" "mysqldump failed - is MariaDB running and the credentials in ${MYCNF_SRC} correct? (${dump_out:-no output})"
    echo "snapshot-db.sh: mysqldump failed for '$name': ${dump_out:-no output}" >&2
    exit 1
fi

size_bytes=$(stat -c%s "$dest_dump" 2>/dev/null || echo 0)
write_status "success" "" "$size_bytes" "${DUMP_IN_CONTAINER#/}"

echo "snapshot-db.sh: db dump ready for '$name' ($dest_dump, $size_bytes bytes)" >&2
