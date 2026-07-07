#!/bin/bash
# Dead-man's switch: larmar om en pull inte lyckats inom THRESHOLD_MIN minuter.
set -euo pipefail
THRESHOLD_MIN=180
STATE_DIR=/var/lib/nspawn-vault/state
NOTIFY_CONF=/etc/nspawn-vault/notify.conf

[ -f "$NOTIFY_CONF" ] && source "$NOTIFY_CONF"

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

now=$(date +%s)
found=0
errors=0

for f in "$STATE_DIR"/*.json; do
    [ -f "$f" ] || continue
    found=$((found+1))
    name=$(basename "$f" .json)
    result=$(python3 -c "import json,sys; d=json.load(open('$f')); print(d.get('result','missing'))" 2>/dev/null || echo missing)
    ts=$(python3 -c "import json,sys,datetime; d=json.load(open('$f')); print(int(datetime.datetime.fromisoformat(d['ts']).timestamp()))" 2>/dev/null || echo 0)
    age=$(( (now - ts) / 60 ))

    if [ "$result" != "success" ] || [ "$age" -gt "$THRESHOLD_MIN" ]; then
        send_alert "${name}: result=${result}, age=${age}min (threshold=${THRESHOLD_MIN}min)"
        errors=$((errors+1))
    else
        echo "OK: ${name} (${age}min sedan)" >&2
    fi
done

[ "$found" -eq 0 ] && echo "Inga state-filer i $STATE_DIR" >&2
exit $errors
