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
# per line, optionally suffixed ":admin"/":user" (bare address = admin,
# backward-compatible with every existing file) - separate from the global
# Pushover/Slack config since different people care about different source
# hosts, and Admin/User recipients get a differently-worded email (see the
# dual send_host_email calls at the end of the host loop below).
send_host_email() {
    local host="$1" body="$2" subject="$3" category="$4"
    local email_file="$ETC_DIR/$host/notify-email"
    [ -f "$email_file" ] || return 0
    local to="" line addr cat
    while IFS= read -r line || [ -n "$line" ]; do
        [[ "$line" =~ ^#|^$ ]] && continue
        addr="${line%%:*}"
        if [[ "$line" == *:* ]]; then
            cat=$(echo "${line#*:}" | tr '[:upper:]' '[:lower:]')
        else
            cat="admin"
        fi
        [ "$cat" = "$category" ] && to="${to:+$to,}$addr"
    done < "$email_file"
    [ -n "$to" ] || return 0
    /usr/libexec/nspawn-vault/send-email.sh \
        "$to" "$subject" "$body" \
        >/dev/null 2>&1 || echo "WARNING: email alert (${category}) to ${host} failed" >&2
}

# Optional per-host "who to contact for help" shown in the friendlier User
# email - independent of who's actually on the notify-email list (e.g. a
# support alias/phone number rather than a personal alert address).
# admin-contact: line 1 = name, line 2 = contact info. Missing file -> both
# vars empty, caller just omits the contact line. NEVER interpolate these
# into a printf FORMAT string, only ever pass as %s ARGUMENTS - they're
# free text from a config file.
read_admin_contact() {
    ADMIN_CONTACT_NAME=""
    ADMIN_CONTACT_INFO=""
    local f="$ETC_DIR/$1/admin-contact"
    [ -f "$f" ] || return 0
    ADMIN_CONTACT_NAME=$(sed -n '1p' "$f")
    ADMIN_CONTACT_INFO=$(sed -n '2p' "$f")
}

# Optional per-host language for the User email template. notify-language:
# one line, "sv" or "en". Missing/invalid -> "sv".
read_host_language() {
    local f="$ETC_DIR/$1/notify-language" lang
    if [ ! -f "$f" ]; then
        echo sv
        return 0
    fi
    lang=$(tr -d '[:space:]' < "$f")
    case "$lang" in
        en) echo en ;;
        *) echo sv ;;
    esac
}

# Repeat-alert backoff: without this, an ongoing incident (e.g. a source
# host down for hours) re-fires Pushover/Slack/email on every single
# 30-minute check-stale.sh run for as long as it stays unresolved - fine
# for a handful of hosts, a real noise problem at ~200. A small marker file
# per (host, container, problem-kind) records the epoch of the last alert
# sent; should_notify() only allows a new alert once ALERT_BACKOFF_HOURS
# has passed since the previous one for that specific problem. 0 disables
# backoff (always alert, matches the pre-2026-07-16 behavior). The exit
# code and journal output are NOT gated by this - only the outbound
# notification channels are, so `systemctl status` still reflects reality
# even while notifications are suppressed.
ALERT_STATE_DIR=/var/lib/nspawn-vault/state/alerted
mkdir -p "$ALERT_STATE_DIR"
ALERT_BACKOFF_HOURS="${ALERT_BACKOFF_HOURS:-6}"
case "$ALERT_BACKOFF_HOURS" in ''|*[!0-9]*) ALERT_BACKOFF_HOURS=6 ;; esac

should_notify() {
    local key="$1" marker="$ALERT_STATE_DIR/$1" last elapsed
    [ "$ALERT_BACKOFF_HOURS" -le 0 ] && return 0
    [ -f "$marker" ] || return 0
    last=$(cat "$marker" 2>/dev/null || echo 0)
    elapsed=$(( (now - last) / 3600 ))
    [ "$elapsed" -ge "$ALERT_BACKOFF_HOURS" ]
}

mark_notified() {
    echo "$now" > "$ALERT_STATE_DIR/$1"
}

clear_notified() {
    rm -f "$ALERT_STATE_DIR/$1"
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
    host_problems_user=""
    lang=$(read_host_language "$host")

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

        key_stale="${host}_${name}_stale"
        if [ "$result" != "success" ] || [ "$age" -gt "$THRESHOLD_MIN" ]; then
            reason="result=${result}, age=${age}min (threshold=${THRESHOLD_MIN}min)"
            errors=$((errors+1))
            if should_notify "$key_stale"; then
                send_alert "${label}: ${reason}"
                host_problems="${host_problems}- ${name}: ${reason}\n"
                if [ "$lang" = en ]; then
                    host_problems_user="${host_problems_user}- ${name}: the backup could not be confirmed as successful\n"
                else
                    host_problems_user="${host_problems_user}- ${name}: backupen kunde inte bekräftas som lyckad\n"
                fi
                mark_notified "$key_stale"
            else
                echo "SUPPRESSED (backoff): ${label}: ${reason}" >&2
            fi
        else
            echo "OK: ${label} (${age}min sedan)" >&2
            clear_notified "$key_stale"
        fi

        # Ransomware-heuristik satt av pull.sh (zfs diff mot föregående
        # snapshot) - oberoende av success/stale-kollen ovan, eftersom en
        # pull kan lyckas helt normalt samtidigt som innehållet är
        # misstänkt krypterat.
        key_ransomware="${host}_${name}_ransomware"
        if [ "$ransomware" = "1" ]; then
            reason="misstänkt ransomware: ${changed} ändrade filer sedan föregående snapshot"
            errors=$((errors+1))
            if should_notify "$key_ransomware"; then
                send_alert "${label}: ${reason}"
                host_problems="${host_problems}- ${name}: ${reason}\n"
                if [ "$lang" = en ]; then
                    host_problems_user="${host_problems_user}- ${name}: an unusually large number of changed files was detected\n"
                else
                    host_problems_user="${host_problems_user}- ${name}: ett ovanligt stort antal ändrade filer upptäcktes\n"
                fi
                mark_notified "$key_ransomware"
            else
                echo "SUPPRESSED (backoff): ${label}: ${reason}" >&2
            fi
        else
            clear_notified "$key_ransomware"
        fi
    done < "${host_dir}containers"

    if [ -n "$host_problems" ]; then
        body=$(printf 'nspawn-vault found a problem with the following container(s) on %s:\n\n%b\nSee the dashboard for details.\n' "$host" "$host_problems")
        send_host_email "$host" "$body" "nspawn-vault: stale backup(s) on ${host}" "admin"
    fi

    if [ -n "$host_problems_user" ]; then
        read_admin_contact "$host"
        contact_line=""
        if [ -n "$ADMIN_CONTACT_NAME" ]; then
            if [ "$lang" = en ]; then
                contact_line=$(printf 'Please contact %s (%s) if you have any questions.\n' "$ADMIN_CONTACT_NAME" "$ADMIN_CONTACT_INFO")
            else
                contact_line=$(printf 'Kontakta %s (%s) om du har frågor.\n' "$ADMIN_CONTACT_NAME" "$ADMIN_CONTACT_INFO")
            fi
        fi
        if [ "$lang" = en ]; then
            subject_user="nspawn-vault: backup problem on ${host}"
            body_user=$(printf 'Hello,\n\nThere was a problem with the backup for the following container(s) on %s:\n\n%b\n%s\nThis is an automated message from nspawn-vault.\n' "$host" "$host_problems_user" "$contact_line")
        else
            subject_user="nspawn-vault: problem med backup på ${host}"
            body_user=$(printf 'Hej,\n\nDet uppstod ett problem med backupen för följande behållare på %s:\n\n%b\n%s\nDetta är ett automatiskt meddelande från nspawn-vault.\n' "$host" "$host_problems_user" "$contact_line")
        fi
        send_host_email "$host" "$body_user" "$subject_user" "user"
    fi
done

[ "$hosts_found" -eq 0 ] && echo "Inga källservrar konfigurerade i $ETC_DIR" >&2
exit $errors
