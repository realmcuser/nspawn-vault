#!/bin/bash
# pre-snapshot.sh <container-name>
#
# Only ever invoked by dispatch.sh (the name is already validated there,
# but re-checked here defensively since this script could in principle be
# run directly). Optional, generic pre-snapshot hook - runs one
# operator-defined command *inside* the container via systemd-run, right
# before the vault's rsync pull. Exists so services with their own backup
# tooling (FreeIPA's ipa-backup, postgres's pg_dump, anything else) can
# hook into pull-backup without dispatch.sh/nspawn-vault having to know
# anything about databases - see snapshot-db.sh for the MariaDB-specific
# equivalent, kept as its own thing since it's widely used enough on its
# own to be worth keeping as a first-class option rather than folding into
# this generic mechanism.
#
# Hook command: /etc/cockpit-nspawn/pull/<name>.hook, a single shell
# command (or short script), mode 600 if it contains anything sensitive,
# created once by whoever sets up pull backups for this container (see
# example.hook in this directory). Root-authored locally on this host and
# never sent by the vault - same trust boundary as snapshot-db.sh's own
# <name>.cnf DB-credentials file: dispatch.sh only ever runs this one
# fixed, whitelisted script name, the actual command text is local config
# the vault never sees or sends.
#
# Containers with no hook configured here are a normal, supported case -
# this just no-ops (exit 0) for them, it does not treat "no hook" as a
# failure.
#
# Not every container has a systemd of its own to connect to - e.g. a
# Boot=no/ProcessTwo=yes container running an old distro (CentOS 7 and
# earlier) with no systemd inside at all. systemd-run then fails outright
# ("Failed to connect to bus") before the hook command ever runs, so this
# falls back to nsenter in that one specific case - see run_in_container()
# below. Confirmed live 2026-11-12 against fhd-kompass on
# vilhelmina-csrv.vpn.fhd.se (CentOS 7.9, no systemd).

set -euo pipefail

NAME_RE='^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'
name="${1:-}"
[[ "$name" =~ $NAME_RE ]] || { echo "pre-snapshot.sh: invalid container name: '$name'" >&2; exit 1; }

STATUS_DIR="/etc/cockpit-nspawn/backup-status"
STATUS_FILE="$STATUS_DIR/${name}-presnapshot.json"

write_status() {
    mkdir -p "$STATUS_DIR"
    printf '{"result":"%s","timestamp":"%s","message":"%s"}\n' \
        "$1" "$(date -Iseconds)" "$(printf '%s' "${2:-}" | tr '"' "'" | tr '\n' ' ')" > "$STATUS_FILE"
}

# Runs $1 as root inside container $name, via systemd-run when the
# container has its own systemd/dbus to connect to, falling back to
# nsenter when it doesn't. The two cases are told apart by systemd-run's
# own error text rather than by probing the container's filesystem for
# /run/systemd/system - that path lives on a per-namespace tmpfs that
# isn't visible from the host's own mount namespace (the exact same
# visibility problem documented in bind-mounted-storage.md for Bind=
# mounts), so a host-side file check would be unreliable here too. Only
# retries via nsenter when systemd-run never actually reached the
# container (bus-connection failure) - a real failure *inside* the
# container is reported as-is, never retried, so a genuinely failing hook
# can't end up silently running twice.
#
# nsenter has no timeout of its own (unlike systemd-run's unit lifecycle),
# hence the explicit `timeout` wrapper - override via HOOK_TIMEOUT_SEC if
# a hook legitimately needs longer than the default.
HOOK_TIMEOUT_SEC="${HOOK_TIMEOUT_SEC:-600}"
run_in_container() {
    # $? right after "if CMD; then ... fi" with no matching else is 0
    # regardless of CMD's real exit status (that's the if-construct's own
    # exit status, per POSIX - not CMD's) - rc is captured in an explicit
    # else instead, where $? is still CMD's own real exit code.
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

HOOK_FILE="/etc/cockpit-nspawn/pull/${name}.hook"
if [ ! -f "$HOOK_FILE" ]; then
    echo "pre-snapshot.sh: no hook configured for '$name' ($HOOK_FILE missing) - skipping" >&2
    exit 0
fi

if ! machinectl show "$name" --property=State 2>/dev/null | grep -q '^State=running'; then
    write_status "failed" "container is not running - cannot run pre-snapshot hook"
    echo "pre-snapshot.sh: container '$name' is not running - cannot run pre-snapshot hook" >&2
    exit 1
fi

hook_cmd=$(cat "$HOOK_FILE")

# --pipe (inside run_in_container's systemd-run call) connects the hook's
# stdio so its own stdout/stderr is actually captured here (--quiet alone
# only suppresses systemd-run's own noise) - without it a failing hook
# would only ever be visible as a bare exit code, with no way to tell the
# vault (or an admin reading the pull log) what actually went wrong.
if ! hook_out=$(run_in_container "$hook_cmd"); then
    write_status "failed" "hook command failed: ${hook_out:-no output}"
    echo "pre-snapshot.sh: hook command failed for '$name': ${hook_out:-no output}" >&2
    exit 1
fi

write_status "success" ""
echo "pre-snapshot.sh: hook completed for '$name'" >&2
