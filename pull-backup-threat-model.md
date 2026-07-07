# Pull-based backup for cockpit-nspawn

Design proposal handed off to Claude Code. Builds on the existing push
model in `BackupDialog.jsx` / `RestoreDialog.jsx`, but inverts the trust
path so a compromised source server can't reach or destroy the backups.

---

## 1. Why (threat model)

Existing push model:

```
[source server]  --(holds SSH key, rsync --delete + rm -rf)-->  [backup]
   ^ gets compromised                                              ^ gets deleted with it
```

The source server is a *guest on the customer's LAN* and can be
compromised. Since it holds the key and has write/delete rights to the
vault, the backups die along with production.

Pull model:

```
[source server]  <--(VAULT initiates, reads read-only)--  [backup vault (trusted)]
   ^ gets compromised            no creds                    ^ holds all the keys
     holds NO backup creds       leave here                    takes ZFS snapshots
     CANNOT reach the vault                                     prunes locally
```

Load-bearing principles:

1. **The vault initiates.** The source server has no outgoing backup creds
   and no path to the vault. A compromised host can't touch the backups.
2. **Read-only on the source side.** Even if the *vault* is compromised, it
   can only *read* source data via a forced-command-locked key — never
   write back.
3. **Immutability on the vault.** ZFS snapshots (read-only, unreachable
   from the source) + an offsite replica. Retention/pruning happens only
   on the trusted side.
4. **Dead-man's switch.** An encrypted/dead host just stops producing
   fresh pulls → the vault alerts. Far more robust than each host
   self-reporting failures.

Transport: everything goes over Tailscale/Headscale, same as today.

---

## 2. Architecture

A **backup vault** (can be central or per region) on the Tailnet. Per
source:

- Its own ed25519 key on the vault (never on the source server).
- A ZFS dataset: `vault/<source>/<container>`, snapshotted after every
  successful pull.
- An offsite tier (a `zfs send` replica *or* append-only restic) for 3-2-1.

The source server only gets two additions:
- A forced-command-locked `authorized_keys` line for the backup user.
- A small dispatcher that allows exactly two operations: `snapshot-db` and
  a read-only rrsync of `/var/lib/machines`.

---

## 3. Source side (minimal, read-only)

### 3.1 Backup user + key

Create a dedicated user (or reuse root but lock the key down hard - the
forced command means the role can only ever read regardless). `restrict`
turns off agent/port/X11 forwarding and pty.

`/home/nspawn-pull/.ssh/authorized_keys` (or root's, locked down):

```
restrict,command="/usr/local/lib/nspawn-pull/dispatch.sh" ssh-ed25519 AAAA...vault's-public-key... nspawn-pull@vault
```

### 3.2 Dispatcher (whitelist)

`/usr/local/lib/nspawn-pull/dispatch.sh` - only lets two commands through:

```bash
#!/bin/bash
set -euo pipefail
cmd="${SSH_ORIGINAL_COMMAND:-}"
ALLOWED_ROOT=/var/lib/machines

case "$cmd" in
    "snapshot-db "*)
        name="${cmd#snapshot-db }"
        # validate against an allowlist of known containers
        case "$name" in
            */*|*..*|"") echo "bad name" >&2; exit 1 ;;
        esac
        exec /usr/local/lib/nspawn-pull/snapshot-db.sh "$name"
        ;;
    "rsync --server --sender"*)
        # rrsync reads SSH_ORIGINAL_COMMAND itself and forces read-only
        exec rrsync -ro "$ALLOWED_ROOT"
        ;;
    *)
        echo "denied: $cmd" >&2
        exit 1
        ;;
esac
```

> `rrsync` lives at `/usr/bin/rrsync` on AlmaLinux 9 (the rsync package).
> If it's not there: `/usr/share/doc/rsync/support/rrsync`. `-ro` =
> read-only.

### 3.3 Application-consistent DB dump (local, the password stays put)

`/usr/local/lib/nspawn-pull/snapshot-db.sh` - runs mysqldump *inside* the
container, exactly like the existing `mysqldumpBlock()`, but the password
lives in a local mode-600 file on the source server and is never sent from
the vault:

```bash
#!/bin/bash
set -euo pipefail
NAME="$1"
MYCNF_SRC="/etc/cockpit-nspawn/pull/${NAME}.cnf"   # [client]\npassword=...  (chmod 600)
[ -f "$MYCNF_SRC" ] || exit 0                       # no DB configured -> no-op

if ! machinectl show "$NAME" --property=State 2>/dev/null | grep -q running; then
    echo "container $NAME not running" >&2; exit 1
fi

DST="/var/lib/machines/${NAME}/root/.np-mycnf"
DUMP="/var/lib/machines/${NAME}/var/tmp/cockpit-nspawn-db.sql"
install -m600 "$MYCNF_SRC" "$DST"
trap 'rm -f "$DST"' EXIT

systemd-run --machine="$NAME" --wait -- \
    bash -c 'mysqldump --defaults-extra-file=/root/.np-mycnf --single-transaction --routines --events --all-databases > /var/tmp/cockpit-nspawn-db.sql'
echo "db snapshot ok: $DUMP" >&2
```

The dump lands inside the container tree, so it rides along with the
rsync pull. No DB password ever leaves the source server.

> Alternative to mysqldump: if `/var/lib/machines` is on btrfs, you could
> take an atomic subvolume snapshot on the source side and pull from that
> instead. mysqldump is simpler and gives logical consistency for InnoDB -
> start there, optimize if needed.

---

## 4. Vault side (trusted, does all the work)

### 4.1 Per-source pull script

`/usr/local/lib/nspawn-vault/pull.sh <source-host> <container> <ssh-key> <dataset>`:

```bash
#!/bin/bash
set -euo pipefail
HOST="$1"; NAME="$2"; KEY="$3"; DATASET="$4"   # e.g. vault/source17/sys900
MNT="/$(zfs get -H -o value mountpoint "$DATASET")"  # or hardcode the vault root
SSH=(ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
STATE="/var/lib/nspawn-vault/state/${DATASET//\//_}.json"
mkdir -p "$(dirname "$STATE")"

fail() { printf '{"result":"failed","ts":"%s","msg":"%s"}\n' "$(date -Iseconds)" "$1" > "$STATE"; exit 1; }

# 1) Ask the source to take a fresh DB dump (forced command only allows this + rrsync)
"${SSH[@]}" "$HOST" "snapshot-db $NAME" || fail "snapshot-db failed"

# 2) Pull read-only into the live dataset. ZFS handles incrementals;
#    no --link-dest needed since snapshots give us the versions.
rsync -aH --delete --numeric-ids \
    -e "${SSH[*]}" \
    "$HOST:/var/lib/machines/$NAME/" "$MNT/" \
    || fail "rsync pull failed"

# 3) Atomic, read-only, unreachable-from-the-source snapshot
zfs snapshot "${DATASET}@$(date +%Y%m%d-%H%M%S)" || fail "zfs snapshot failed"

printf '{"result":"success","ts":"%s"}\n' "$(date -Iseconds)" > "$STATE"
```

Run per source via a systemd timer on the vault (e.g. every 30 min,
staggered).

### 4.2 GFS retention - local, on the vault

Reuse your existing GFS logic, but against `zfs list -t snapshot` instead
of remote `rm`. Skeleton:

```bash
#!/bin/bash
# prune.sh <dataset> <hourly> <daily> <weekly> <monthly> <yearly>
set -euo pipefail
DATASET="$1"; shift
zfs list -H -o name -t snapshot -s creation "$DATASET" \
  | sed "s#^${DATASET}@##" \
  | python3 /usr/local/lib/nspawn-vault/gfs.py "$@" \
  | while read -r snap; do zfs destroy "${DATASET}@${snap}"; done
```

`gfs.py` = essentially your GFS_PYTHON, but it reads snapshot names from
stdin and *prints the ones to delete* (instead of deleting files itself).
That makes the actual deletion an explicit `zfs destroy` on the trusted
side.

### 4.3 Dead-man's switch (the most important alert)

A separate timer on the vault that scans every `state/*.json` and alerts
if some source hasn't produced a fresh successful pull within the
threshold. An encrypted/dead host *can't* self-report - it just stops
showing up here.

```bash
#!/bin/bash
set -euo pipefail
THRESHOLD_MIN=180
now=$(date +%s)
for f in /var/lib/nspawn-vault/state/*.json; do
    ts=$(python3 -c 'import json,sys,datetime;d=json.load(open(sys.argv[1]));print(int(datetime.datetime.fromisoformat(d["ts"]).timestamp()))' "$f" 2>/dev/null || echo 0)
    res=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["result"])' "$f" 2>/dev/null || echo missing)
    age=$(( (now - ts) / 60 ))
    if [ "$res" != success ] || [ "$age" -gt "$THRESHOLD_MIN" ]; then
        # reuse your existing send_notification (SMTP/Slack/Pushover)
        notify "STALE BACKUP: $(basename "$f") result=$res age=${age}min"
    fi
done
```

---

## 5. Offsite tier (3-2-1, ransomware-resistant)

Pick one (or both):

**A. `zfs send` to a receive-only replica** (cleanest):
```bash
zfs send -I "${DATASET}@<previous>" "${DATASET}@<latest>" \
  | ssh offsite "zfs recv -F vaultmirror/<source>/<container>"
```
The offsite box only ever receives; it has no creds to write back.
Ideally at a different physical location / different cloud.

**B. restic to object storage with append-only / object-lock** (cloud):
```bash
restic -r b2:source-vault backup "$MNT/.zfs/snapshot/<latest>"
```
Use an **append-only restic key** (`--append-only` on the REST/rclone
server, or B2 Object Lock). That way a compromised vault server *can't*
delete the history.

The point: even if the vault itself goes down, at least one copy stays
physically undeletable.

---

## 6. Restore (the fast path)

Restore needs write access to the source server - but that access should
**not** be the read-only backup key. Deliberately activate a separate
restore path when it's actually needed (interactive admin SSH over
Tailscale, or a second key that's normally switched off). That way there's
never a standing credential that can write data in either direction.

Restoring a whole container:

```bash
# 1) Pick a snapshot on the vault
zfs rollback "${DATASET}@<snapshot>"          # or mount .zfs/snapshot/<x> read-only

# 2) Rsync back to the source (restore path, not the backup key)
machinectl stop "$NAME" 2>/dev/null || true
rsync -aH --delete "$MNT/" "source:/var/lib/machines/$NAME/"

# 3) Start it and import the DB dump from inside
machinectl start "$NAME"
systemd-run --machine="$NAME" --wait -- \
    bash -c 'mysql < /var/tmp/cockpit-nspawn-db.sql'
```

Measure and document RTO per source - a backup whose restore has never
been tested is just a hypothesis.

---

## 7. Integration into cockpit-nspawn

The pull logic lives on the **vault**, not on the source server. Suggested
split:

- **Source server (existing cockpit-nspawn):** add a simple
  *"Enable pull backup"* toggle that installs `dispatch.sh`,
  `snapshot-db.sh`, the forced-command line in `authorized_keys`, and (if
  there's a DB) writes `*.cnf`. Nothing more.
- **Vault (new Cockpit page or its own `nspawn-vault` module):** an
  overview page listing every source, latest successful pull, snapshot
  count, offsite status, and stale alerts. The pull/prune/dead-man timers
  live here.
- **Keep the push model** as-is for anyone who wants it - pull becomes the
  secure default for the fleet. `RestoreDialog` can be extended to
  recognize "pull"-configured containers and fetch from the vault instead.

The config format can mirror the existing
`/etc/cockpit-nspawn/backup/<name>.json`, but with `mode: "pull"` and the
vault-side fields (`dataset`, `gfs_*`, offsite target).

---

## 8. Rollout order (low risk → high leverage)

1. Set up the vault + ZFS + one test source. Verify pull → snapshot →
   restore.
2. Add the dead-man's alert *before* trusting the system.
3. Migrate sources one at a time; run pull alongside the existing push
   until you've seen one successful restore per source.
4. Add the offsite tier (append-only) once the on-site flow is stable.
5. Turn off the push keys on migrated sources - that's when the standing
   write access disappears from the source servers entirely.

The result: a compromised source server can encrypt its own production,
but has neither the key nor a path to the backups, and the vault alerts
within minutes once pulls stop coming in. Restore becomes a manageable
event instead of a disaster.
