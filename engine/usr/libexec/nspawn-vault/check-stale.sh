#!/bin/bash
# Dead-man's switch: larmar om en pull inte lyckats inom THRESHOLD_MIN minuter.
set -euo pipefail
THRESHOLD_MIN=180
STATE_DIR=/var/lib/nspawn-vault/state
ETC_DIR=/etc/nspawn-vault
NOTIFY_CONF="$ETC_DIR/notify.conf"
POOL="${NSPAWN_VAULT_POOL:-vault}"

[ -f "$NOTIFY_CONF" ] && source "$NOTIFY_CONF"

# Per-container alert - unchanged behavior, still fires immediately for
# every problem container individually.
send_alert() {
    local msg="$1"
    echo "ALERT: $msg" >&2
    if [ -n "${PUSHOVER_TOKEN:-}" ] && [ -n "${PUSHOVER_USER:-}" ]; then
        curl -s https://api.pushover.net/1/messages.json \
            -F "token=${PUSHOVER_TOKEN}" \
            -F "user=${PUSHOVER_USER}" \
            -F "title=nspawn-vault: stale backup" \
            -F "message=${msg}" \
            -F "priority=1" >/dev/null 2>&1 || true
    fi
    if [ -n "${SLACK_URL:-}" ]; then
        curl -s -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\"ALERT: ${msg}\"}" \
            "${SLACK_URL}" >/dev/null 2>&1 || true
    fi
}

# One batched email per source host (not per container) - with ~200 hosts x
# 2+ containers each, per-container emails would flood the two recipients
# configured for a host. Recipients live in <host>/notify-email, one address
# per line - separate from the global Pushover/Slack config since different
# people care about different source hosts.
send_host_email() {
    local host="$1" body="$2"
    local email_file="$ETC_DIR/$host/notify-email"
    [ -f "$email_file" ] || return 0
    local to
    to=$(paste -sd, "$email_file" 2>/dev/null)
    [ -n "$to" ] || return 0
    /usr/libexec/nspawn-vault/send-email.sh \
        "$to" \
        "nspawn-vault: stale backup(s) on ${host}" \
        "$body" >/dev/null 2>&1 || echo "WARNING: email alert to ${host} failed" >&2
}

now=$(date +%s)
errors=0
hosts_found=0

# Loop configured hosts/containers (same source pull-host.sh uses) instead
# of globbing $STATE_DIR/*.json - the state filename alone
# (vault_<host-short>_<container>.json) can't be split back into an exact
# host/container pair (either half may itself contain underscores), and we
# need the real host to know which notify-email file to send to.
for host_dir in "$ETC_DIR"/*/; do
    [ -f "${host_dir}containers" ] || continue
    host=$(basename "$host_dir")
    hosts_found=$((hosts_found+1))
    host_short="${host%%.*}"
    host_problems=""

    while IFS= read -r name || [ -n "$name" ]; do
        [[ "$name" =~ ^#|^$ ]] && continue
        dataset="${POOL}/${host_short}/${name}"
        f="$STATE_DIR/${dataset//\//_}.json"
        label="${host}/${name}"

        if [ -f "$f" ]; then
            result=$(python3 -c "import json,sys; d=json.load(open('$f')); print(d.get('result','missing'))" 2>/dev/null || echo missing)
            ts=$(python3 -c "import json,sys,datetime; d=json.load(open('$f')); print(int(datetime.datetime.fromisoformat(d['ts']).timestamp()))" 2>/dev/null || echo 0)
            ransomware=$(python3 -c "import json; d=json.load(open('$f')); print('1' if d.get('ransomware_suspected') else '0')" 2>/dev/null || echo 0)
            changed=$(python3 -c "import json; d=json.load(open('$f')); print(d.get('changed_entries', 0))" 2>/dev/null || echo 0)
        else
            result=missing
            ts=0
            ransomware=0
            changed=0
        fi
        age=$(( (now - ts) / 60 ))

        if [ "$result" != "success" ] || [ "$age" -gt "$THRESHOLD_MIN" ]; then
            reason="result=${result}, age=${age}min (threshold=${THRESHOLD_MIN}min)"
            send_alert "${label}: ${reason}"
            host_problems="${host_problems}- ${name}: ${reason}\n"
            errors=$((errors+1))
        else
            echo "OK: ${label} (${age}min sedan)" >&2
        fi

        # Ransomware-heuristik satt av pull.sh (zfs diff mot föregående
        # snapshot) - oberoende av success/stale-kollen ovan, eftersom en
        # pull kan lyckas helt normalt samtidigt som innehållet är
        # misstänkt krypterat.
        if [ "$ransomware" = "1" ]; then
            reason="misstänkt ransomware: ${changed} ändrade filer sedan föregående snapshot"
            send_alert "${label}: ${reason}"
            host_problems="${host_problems}- ${name}: ${reason}\n"
            errors=$((errors+1))
        fi
    done < "${host_dir}containers"

    if [ -n "$host_problems" ]; then
        body=$(printf 'nspawn-vault found a problem with the following container(s) on %s:\n\n%b\nSee the dashboard for details.\n' "$host" "$host_problems")
        send_host_email "$host" "$body"
    fi
done

[ "$hosts_found" -eq 0 ] && echo "Inga källservrar konfigurerade i $ETC_DIR" >&2
exit $errors
