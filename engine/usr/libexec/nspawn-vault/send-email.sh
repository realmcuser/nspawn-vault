#!/bin/bash
# Sends one email via curl+SMTP, using the relay config in notify.conf.
# Usage: send-email.sh <to>[,<to>...] <subject> <body>
#
# Shared by check-stale.sh (real dead-man's-switch alerts, batched one email
# per source host) and nspawn-vault-web's "send test email" admin button
# (which calls this same script directly, so a successful test really does
# prove the relay config works end to end - not just that curl was
# reachable).
set -euo pipefail
NOTIFY_CONF=/etc/nspawn-vault/notify.conf
[ -f "$NOTIFY_CONF" ] && source "$NOTIFY_CONF"

TO="${1:?usage: send-email.sh <to> <subject> <body>}"
SUBJECT="${2:?usage: send-email.sh <to> <subject> <body>}"
BODY="${3:?usage: send-email.sh <to> <subject> <body>}"

if [ -z "${SMTP_HOST:-}" ]; then
    echo "SMTP_HOST not configured in $NOTIFY_CONF" >&2
    exit 1
fi

PORT="${SMTP_PORT:-587}"
TLS_MODE="${SMTP_TLS_MODE:-starttls}"
FROM="${SMTP_FROM:-nspawn-vault@$(hostname -f 2>/dev/null || hostname)}"

if [ "$TLS_MODE" = "implicit" ]; then
    URL="smtps://${SMTP_HOST}:${PORT}"
    TLS_OPTS=()
else
    URL="smtp://${SMTP_HOST}:${PORT}"
    TLS_OPTS=(--ssl-reqd)
fi

AUTH_OPTS=()
if [ -n "${SMTP_USER:-}" ]; then
    AUTH_OPTS=(--user "${SMTP_USER}:${SMTP_PASS:-}")
fi

# --mail-rcpt needs one address per flag, not a comma-joined string -
# TO may be a comma-separated list (e.g. two recipients for one host).
RCPT_OPTS=()
IFS=',' read -ra ADDRS <<< "$TO"
for addr in "${ADDRS[@]}"; do
    addr="${addr// /}"
    [ -n "$addr" ] && RCPT_OPTS+=(--mail-rcpt "$addr")
done
if [ "${#RCPT_OPTS[@]}" -eq 0 ]; then
    echo "no recipient address given" >&2
    exit 1
fi

MSG=$(mktemp)
trap 'rm -f "$MSG"' EXIT
{
    echo "From: ${FROM}"
    echo "To: ${TO}"
    echo "Subject: ${SUBJECT}"
    echo "Date: $(date -R)"
    echo "Content-Type: text/plain; charset=UTF-8"
    echo
    echo "${BODY}"
} > "$MSG"

if curl -s -S --url "$URL" \
    "${TLS_OPTS[@]}" \
    "${AUTH_OPTS[@]}" \
    --mail-from "${FROM}" \
    "${RCPT_OPTS[@]}" \
    --upload-file "$MSG"; then
    logger -t nspawn-vault-email "sent to ${TO}: ${SUBJECT}"
else
    rc=$?
    logger -t nspawn-vault-email "FAILED to ${TO} (curl exit ${rc}): ${SUBJECT}"
    exit "$rc"
fi
