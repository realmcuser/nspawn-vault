# nspawn-vault — Design Document

Standalone web UI for the backup vault (NOT a Cockpit module — see decision
below). Builds on the infrastructure in `/usr/libexec/nspawn-vault/` on the
vault server.

See also: `nspawn-pull-backup-design.md` (threat model and architecture)

---

## Decision: standalone web UI, not a Cockpit module (2026-07-01)

Originally planned as a Cockpit module (`cockpit-nspawn-vault`), but
decided to build a fully standalone web frontend instead, for two reasons:

1. **Cleaner overviews**: the vault needs to be able to show clear warning
   signals (large red alerts when a source's pull backups have stopped
   working) without having to conform to Cockpit's UI framework and its
   constraints.
2. **No Cockpit dependency**: the vault is already a separate server with
   its own purpose (receive pull backups, manage ZFS, alert) — doesn't
   need `machinectl` or any of Cockpit's container-management
   functionality.

Planned stack: same pattern as the internal build tool (FastAPI backend +
simple frontend), with Caddy as reverse proxy/TLS in front. Not started
yet — pure future planning. Authentication (since Cockpit's PAM login
isn't available for free anymore) is unresolved and needs deciding once
the UI actually starts getting built.

The source side (dispatcher + authorized_keys in
`/usr/local/lib/nspawn-pull/` on the nspawn host, unchanged today) stays
tied to the cockpit-nspawn packaging, though — see "Source side in
cockpit-nspawn (toggle)" below. It's only the vault's OWN admin surface
that becomes standalone.

---

## Vault OS: switching from Ubuntu to AlmaLinux (planned, 2026-07-01)

The current vault (192.0.2.10) runs Ubuntu 26.04 + native
`zfsutils-linux` and keeps doing so for now — it's the reference/
production system in the meantime.

New plan: an AlmaLinux 10 libvirt/KVM VM (built on the build host) with
ZFS via OpenZFS's official DKMS repo for EL, to get the same familiar
RHEL stack as the rest of the environment. The reason Ubuntu was chosen
originally (ZFS needs DKMS on RHEL due to the CDDL/GPL license conflict,
Ubuntu has it built in) still stands as the reason *why* this isn't
trivial — but it's judged worth the trouble for the sake of consistency.

**RPM packaging already sketched and build-tested locally**:
`/root/nspawn-vault/engine/` (on the build host — moved there 2026-07-03
when `nspawn-vault` and `nspawn-vault-web` were merged into one shared
development directory, see `CLAUDE.md`), with `nspawn-vault.spec`.
Contains:

- Scripts moved from `/usr/local/lib` (wrong FHS location for packaged
  software) to `/usr/libexec/nspawn-vault/`
- **Bug fix**: `pull.sh`'s dataset default was hardcoded to
  `vault/source0/...` regardless of `$HOST` — fixed to derive it from
  `${HOST%%.*}` instead, otherwise multiple source servers collide in the
  same dataset namespace
- Systemd **template unit** `nspawn-vault-pull@.service`/`.timer`
  replaces hand-written per-host files — new source: `systemctl enable
  --now nspawn-vault-pull@<host>.timer`
- `setup-zfs.sh` and `init-pool.sh` as explicit manual first-run steps
  (deliberately NOT in `%post` — adding repos and creating a pool are too
  sensitive to do silently)
- **Fully verified in practice on 2026-07-01** against a real AlmaLinux
10.2 VM (198.51.100.20, built on the build host): `setup-zfs.sh` run
(found the right repo URL after the first guess 404'd —
`zfs-release-3-0.el10`, not `2-3`), `init-pool.sh /dev/vdb` run, RPM built
via the internal build tool (its own project created,
`nspawn-vault-1.0.0-2.el10.noarch.rpm`) and installed cleanly with `dnf
install <url>` — `Requires: zfs` resolved automatically. SSH key
generated, the source side's `authorized_keys` on source0 extended
additively (a new line, doesn't touch the production vault's existing
key), container list + timers configured, a full pull cycle against
`testapp1` ran and returned `{"result":"success",...}` with a 1.49 GB ZFS
snapshot. Full step-by-step documentation in
`/root/nspawn-vault/engine/README.md` (written to become the GitHub
project's landing page).

Source tree exists as a tarball at `/root/nspawn-vault.tar.gz`, an
internal build tool project created for it (separate from
cockpit-nspawn's project_id 4).

---

## Future: replication between multiple vaults (syncoid) — just a thought

If more vaults (2-3) get set up for redundancy: the recommendation is
`zfs send | zfs receive` via `syncoid` (the sanoid project's wrapper), not
a block-level mirror vdev (ZFS mirrors aren't designed for separate
hosts/WAN). This reuses the same snapshots already taken by the GFS
schedule.

**Important to remember**: this is asynchronous replication, not
synchronous mirroring — a secondary vault is always one pull cycle behind
(currently ~30 min). Gives redundancy/DR, not failover without a
data-loss window. Nothing decided, nothing to build right now.

---

## Infrastructure on the vault (becomes the future web UI's backend)

### Directory structure on the vault

The current Ubuntu vault (192.0.2.10) still uses `/usr/local/lib/`. The
new RPM packaging (see above) moves this to `/usr/libexec/nspawn-vault/`
— the structure below shows the new, packaged layout:

```
/etc/nspawn-vault/
├── notify.conf                  # Pushover/Slack creds for vault alerts
└── source0.example.com/
    └── containers               # One container per line

/usr/libexec/nspawn-vault/
├── pull.sh                      # Pull one container: pull.sh <host> <name>
├── pull-host.sh                 # Pull all containers for a host
├── check-stale.sh               # Dead-man's switch
├── gfs-prune.sh                 # GFS retention via zfs destroy
├── gfs.py
├── prune-all.sh
├── setup-zfs.sh                 # manual, one-time, NOT in %post
└── init-pool.sh                 # manual, one-time, NOT in %post

/var/lib/nspawn-vault/state/
└── vault_<host>_<name>.json     # {"result":"success","ts":"...","snap":"..."}

/vault/                          # ZFS pool (name configurable via NSPAWN_VAULT_POOL)
└── source0/
    └── testapp1/                # dataset, snapshots: @20260630-210156
```

### Systemd timers on the vault

- `nspawn-vault-pull@<host>.timer` — templated unit, one instance per
  source server
- `nspawn-vault-check.timer` — runs check-stale.sh every 30 min
  (dead-man's switch)
- `nspawn-vault-prune.timer` — runs prune-all.sh daily at 04:00 (GFS
  retention)

---

## What the standalone web UI should show

### Main view — source server overview

Table with one row per configured source server:

| Server | Containers | Latest pull | Status | ZFS pool |
|--------|-----------|-------------|--------|----------|
| source0.example.com | 5 | 2026-06-30 21:01 | OK | vault/source0 |

### Per-server detail view — containers

Expandable row with a table per container:

| Container | Latest pull | Snapshot | Size | Next pull |
|-----------|-------------|---------|---------|-----------|
| testapp1  | 21:01 OK    | @20260630-210156 | 1.5 GB | 21:31 |

### Snapshot history

Modal with a list of all ZFS snapshots for a container (`zfs list -t
snapshot`). Button to start the restore flow.

### Configuration

- Add/remove source servers
- Configure pull interval per server
- GFS retention levels
- Notification channels (Pushover/Slack/SMTP) for the dead-man's switch

---

## Source side in cockpit-nspawn (toggle)

In `MachineActions.jsx` or `BackupDialog.jsx`: a checkbox/toggle *"Enable
pull backup from the vault"* that:

1. Installs `/usr/local/lib/nspawn-pull/dispatch.sh`
2. Installs `/usr/local/lib/nspawn-pull/snapshot-db.sh`
3. Adds the forced-command line to `/root/.ssh/authorized_keys`
4. Shows the vault's public key for the user to paste onto the vault

---

## Technology choices

- **ZFS**: native snapshots, immutable from the source, efficient
  incrementals with `zfs send`
- **rrsync -ro**: read-only rsync on the source side, minimal attack
  surface
- **ed25519**: one key per vault (not per source), never on the source
  server
- **systemd timers**: no cron, journal logging, easy to monitor
- **State JSON**: simple file-based status, easy to read from the
  standalone web UI

---

## Remaining to build (manual phase)

- [x] ZFS pool + dataset
- [x] pull.sh + dispatcher + rrsync on the source side
- [x] Tested pulling all 5 containers from source0
- [x] pull-host.sh (pull all containers for a host)
- [x] Systemd service + timer for automatic pulls (every 30 minutes)
- [x] check-stale.sh (dead-man's switch, 3h threshold)
- [x] Systemd timer for check-stale (every 30 minutes)
- [x] gfs-prune.sh + gfs.py (GFS retention on ZFS snapshots)
- [x] Systemd timer for prune (daily at 04:00)
- [x] notify.conf + Pushover notification verified
- [x] Per-container DB handling

## DB handling in the pull variant — DONE and TESTED (2026-07-01)

### Problem
rsync of `/var/lib/machines/<name>/` while the DB is running → risk of
inconsistent database files.

### Solution: per-container config on the source side

File: `/etc/cockpit-nspawn/pull/<name>.conf` (chmod 600, directory chmod
700)

```bash
# MariaDB/MySQL — dump while the container is running (recommended)
DB_TYPE=mariadb
DB_USER=root            # default: root
DB_PASSWORD=secret       # empty if the auth plugin allows it (e.g. mysql_native_password with no password)

# PostgreSQL — dump while the container is running
DB_TYPE=postgres
DB_USER=postgres        # default: postgres
DB_PASSWORD=secret

# Stop the container during backup (optional, works regardless of DB_TYPE or without one)
# Handy for pgapp1 (PostgreSQL, fine to stop at 02:00)
STOP_DURING_BACKUP=true
```

### Behavior in snapshot-db.sh / restore-after-backup.sh depending on config

| DB_TYPE | STOP_DURING_BACKUP | Action |
|---------|-------------------|--------|
| mariadb | false | mysqldump (--defaults-extra-file) inside the container → sql file rides along with rsync |
| postgres | false | pg_dumpall (PGPASSWORD) inside the container → sql file rides along with rsync |
| any/none | true | machinectl stop → rsync → machinectl start (via trap in pull.sh) |
| (no .conf) | — | rsync directly, no DB handling (no-op, as before) |

### Implementation

- `dispatch.sh` on the source side now also whitelists
  `restore-after-backup <name>` (in addition to `snapshot-db <name>` and
  the rsync command), same name validation (`*/*|*..*|""|*" "*` gets
  rejected).
- `snapshot-db.sh` reads the `.conf` file if it exists; sources
  `DB_TYPE`, `DB_USER`, `DB_PASSWORD`, `STOP_DURING_BACKUP`. When
  `STOP_DURING_BACKUP=true`, the container gets stopped (polls
  `machinectl show` until it disappears from the registry, max 30s) and
  the function returns without a dump. Otherwise mysqldump/pg_dumpall
  runs depending on `DB_TYPE`.
- `restore-after-backup.sh` (new) reads the same `.conf` file, removes
  the dump file (`/var/tmp/cockpit-nspawn-db.sql`) from the live
  container if `DB_TYPE` was set — it shouldn't sit unencrypted in
  production any longer than necessary for rsync to have pulled it — and
  starts the container back up if `STOP_DURING_BACKUP=true`. The dump
  stays around (versioned) in the vault's ZFS snapshots.
- `pull.sh` on the vault sets a `trap ... EXIT` right after the
  `snapshot-db` call that always invokes `restore-after-backup` at the
  end of the script — guarantees a restart even if the rsync or
  zfs-snapshot step fails.

### Tested and verified (source0 → vault 192.0.2.10)

- **dbapp1** (MariaDB 10.5, root with no password,
  `mysql_native_password`): `DB_TYPE=mariadb`, `DB_USER=root`,
  `DB_PASSWORD=` (empty) → mysqldump produced a 41 MB sql file at
  `/var/tmp/cockpit-nspawn-db.sql`, rode along with rsync, container
  stayed running the whole time.
- **pgapp1** (PostgreSQL 16): `STOP_DURING_BACKUP=true` → container got
  stopped, rsync ran against a quiescent filesystem, ZFS snapshot taken,
  container restarted automatically via the trap, PostgreSQL was
  listening on 5432 again after ~3s.
- **testapp1** (no `.conf` file): the no-op path works unchanged, plain
  rsync with no DB handling.

---

## Standalone web UI (nspawn-vault-web) — BUILT and verified (2026-07-02)

`/root/nspawn-vault/web/` on the build host (moved there 2026-07-03, see
`CLAUDE.md`), 9 phases completed and verified end-to-end (every phase
tested against real data on the AlmaLinux 10 VM at 198.51.100.20 before
starting the next):

```
nspawn-vault-web/
├── backend/
│   ├── main.py, auth_routes.py, auth_utils.py, database.py, models.py,
│   │   ldap_service.py          # auth layer, ported from the internal build tool (SQLite+WAL instead of Postgres)
│   └── vault_config.py, vault_state.py, vault_zfs.py,
│       vault_systemd.py, vault_routes.py   # nspawn-vault-specific domain logic
├── frontend/src/
│   ├── pages/{Login,Dashboard,HostDetail,Admin}.jsx
│   ├── components/{StaleAlertBanner,StatusBadge,Spinner}.jsx
│   ├── components/layout/{Layout,Sidebar}.jsx
│   ├── context/AuthContext.jsx, services/api.js
│   └── locales/{en,sv}/translation.json
├── Caddyfile, env.example, systemd/nspawn-vault-web.service
└── nspawn-vault-web.spec
```

**Auth**: copied almost verbatim from the internal build tool (JWT,
argon2, local-then-LDAP fallback, admin bootstrap via
first-user-becomes-admin), with two deliberate improvements over the
original: `bind_password` is masked (`********`) in `GET
/api/admin/ldap` instead of leaking in plaintext, and the LDAP group's
admin membership gets re-validated on **every** login (not just on first
auto-provisioning like in the internal build tool). SQLite+WAL instead of
Postgres — decided together with the user, no extra service needed just
for a handful of admin accounts.

**Domain logic** (`vault_state.py`) ports `check-stale.sh`'s exact logic
(THRESHOLD_MIN=180, the same state-JSON path construction as `pull.sh`).
**Phase 9 acceptance test**: ran `check-stale.sh` and the web API side by
side against both healthy and artificially stale/failed data — exact
agreement in every case.

**StaleAlertBanner**: solid `bg-red-600`, `w-8 h-8` icon, deliberately
escalated well past the internal build tool's own (pale/small) red
warning style — see the component code for the full Tailwind recipe.

**Bugs found and fixed during the build** (worth remembering):
- The `NextElapseUSecRealtime` pattern (from `BackupsOverview.jsx`) has a
  hidden bug: `date -d ""` (empty string, no scheduled run) does NOT
  fail, it silently gets parsed as "today" — has to check for an empty
  string explicitly before calling `date -d`, otherwise you get a bogus
  but valid epoch value.
- The build tool's `AuthContext.login()` only set `{username}` on the
  `user` object, not the full user record (incl. `role`) — made the
  admin menu invisible right after login until the page got reloaded.
  Fixed: `login()` now calls `fetchCurrentUser()` after a successful
  login.
- **RPM packaging of the vendored venv**: `%global debug_package %{nil}`
  is required (empty debuginfo packages crash the build), and
  `venv/bin/uvicorn` and other wrapper scripts have the build path baked
  into their shebang — broken the moment the venv gets copied to the
  buildroot. Solved with `python3 -m uvicorn` in the systemd unit
  (sidesteps the wrapper scripts entirely) + `%__requires_exclude_from`/
  `%__provides_exclude_from` (otherwise RPM's dependency scanner leaks
  the build path in as a broken `Requires`).
- **The build has to happen on the target's Python version**: tested
  concretely — a local Fedora 44 build (Python 3.14) is incompatible
  with AlmaLinux 10 (Python 3.12) because the site-packages path is
  version-specific. Solved by installing `rpm-build`+`gcc`+
  `python3-devel` and building directly on the VM.

**Verified installed and running** on 198.51.100.20: `dnf install` of the
finished RPM, the service starts cleanly, serves both `/api/*` and the
built frontend (FastAPI's own static fallback).

**Caddy verified 2026-07-02**: installed from EPEL, `Caddyfile` with
`http://` (plain HTTP, no ACME/TLS — LAN is enough, no WAN access needed)
reverse-proxies `/api/*` to `127.0.0.1:8000` and serves the frontend on
`:80`. Confirmed reachable over the LAN (`curl http://198.51.100.20/...`
from the build host) while uvicorn still only listens on `127.0.0.1` —
only Caddy is exposed to the network. Driven by the need for colleagues
to be able to log in and manage the vault, not just the developer.

## Role-based editing: GFS + notifications via Admin (2026-07-02)

Decision: the "user" role stays strictly read-only (Dashboard/HostDetail,
already built), but the "admin" role (local or LDAP — same `role` field,
already built in phase 1/6) should be able to edit GFS retention and
notifications directly in the UI instead of hand-editing files on the
vault.

**Backend**: `vault_config.write_gfs_conf()` and `write_notify_conf()`
(new), `read_notify_conf_masked()` for the admin view (the same
`********` sentinel pattern as LDAP `bind_password` — secrets never leak
in plaintext to the frontend). New endpoints, all `get_current_admin`-
protected: `PUT /api/admin/settings/gfs`, `GET/PUT
/api/admin/settings/notify`.

**Security-critical finding during the build**: `check-stale.sh` does
`source "$NOTIFY_CONF"` as root — if a Pushover token/Slack URL gets
written to the file unprotected and contains shell metacharacters
(`$()`, backticks, etc.), it could execute arbitrary code the next time
the dead-man's switch runs. Solved with `_shell_quote()` (single-quoting
+ escaping embedded quotes) in `write_notify_conf()`. **Tested
concretely**: entered `$(touch /tmp/PWNED)` as a token via the API,
verified the file on disk got the correctly single-quoted value,
actually ran `source` on it — no file got created, the value came out as
a plain text string. Also confirmed the `********` sentinel preserves
real secrets on a subsequent PUT with no change.

**Frontend**: new "GFS Retention" and "Notifications" sections in
`Admin.jsx` (same card layout as the LDAP section). `Dashboard.jsx`'s old
"edit on disk" hint replaced with role-aware text: admin sees a link to
the Admin page, user sees "ask an admin to change this".

## Source server/container list via Admin (2026-07-02)

Built as a follow-up: full CRUD for source servers and their container
lists in the Admin page, admin-protected (`get_current_admin`).

**Backend** (`vault_config.py`): `create_host()`, `delete_host()`,
`write_containers()` — a hostname becomes a directory path and a
container name becomes lines in a file + a ZFS dataset segment, so both
are validated strictly against path traversal/shell-dangerous characters
(`_HOSTNAME_RE`, `_CONTAINER_NAME_RE`) before anything touches the
filesystem. **Tested concretely**: `../../etc/evil` as a hostname and
`../evil` as a container name — both rejected with 400.

`delete_host()` **only removes the pull configuration** (the directory
under `/etc/nspawn-vault/`) — never touches ZFS datasets or snapshots.
Existing backups for a removed host stay in place, only future pulls
stop. This is explicitly documented in the UI (confirmation dialog on
deletion).

**Timer control** (`vault_systemd.py`): `enable_pull_timer()`/
`disable_pull_timer()` — `systemctl enable/disable --now
nspawn-vault-pull@<host>.timer`. Creating a host does NOT enable the
timer automatically (SSH trust on the source side has to be set up
manually first, see the source-toggle section above) — admin flips it on
separately once that's done, a clear UI message about this shows in the
"Add host" form.

**Tested end-to-end** (create → edit container list → enable timer →
delete → verify the timer got disabled and the production host
`source0.example.com` stayed untouched throughout the whole flow), both
directly against the backend and through Caddy on port 80.

**Not built (deliberately, see v1 scope)**: ZFS snapshot browsing/restore
UI, multi-vault replication UI.

Main focus in the UI (per the decision above): a very clear, eye-catching
warning when a source's pull backups have stopped working — not just a
discreet badge like the Cockpit pattern, but something impossible to
miss.
