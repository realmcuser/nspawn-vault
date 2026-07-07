# nspawn-vault

Pull-based backup vault for [cockpit-nspawn](https://github.com/realmcuser/cockpit-nspawn)
hosts. The vault initiates every backup over SSH with a forced, read-only command on
the source host - a compromised nspawn host never holds credentials that could reach
or modify the vault. Backups land on ZFS, snapshotted after every pull, with GFS
retention and a dead-man's switch that alerts via Pushover/Slack if a source host
stops reporting successful pulls.

Tested and verified end-to-end on AlmaLinux 10.2 (2026-07-01): ZFS via OpenZFS DKMS,
RPM install, SSH key setup, and a full pull cycle against a production nspawn host.

## Architecture

**Source host** (nspawn-cockpit, `/usr/local/lib/nspawn-pull/`):
`dispatch.sh`, `snapshot-db.sh`, `restore-after-backup.sh` - a forced SSH command
that only ever runs a whitelisted set of operations for the vault's key.

**Vault** (this project, `/usr/libexec/nspawn-vault/`):
`pull-host.sh` / `pull.sh` initiate the pull over SSH (read-only rsync via
`rrsync -ro` on the source side), `check-stale.sh` runs the dead-man's switch,
`gfs-prune.sh` / `prune-all.sh` apply retention. Results land as ZFS snapshots
under `vault/<host>/<container>`.

The vault pulls - it initiates every connection. The source host never holds a key
that could reach or modify the vault; the vault's key on the source host can only
ever run the forced dispatcher command, nothing else.

## Prerequisites

- AlmaLinux 10 (or another EL10 derivative) for the vault host
- A dedicated block device for the ZFS pool (e.g. `/dev/vdb`)
- One or more nspawn-cockpit hosts already reachable over SSH, with
  `/usr/local/lib/nspawn-pull/dispatch.sh` installed (currently manual - a toggle in
  cockpit-nspawn itself is planned but not built yet)

## Setup

### 1. Install ZFS (before installing this package)

This package `Requires: zfs`, which is not in AlmaLinux's own repos (CDDL/GPL license
conflict). Installing ZFS has to happen *before* this RPM, or dnf can't resolve the
dependency - so the setup script ships in its own package, `nspawn-vault-zfs-bootstrap`
(`../zfs-bootstrap/`), which has no dependencies of its own and can therefore always be
installed first, on a completely fresh box:

```bash
dnf install nspawn-vault-zfs-bootstrap-<version>.<dist>.noarch.rpm
nspawn-vault-setup-zfs
```

This adds the OpenZFS repo (`zfs-release-3-0.el10.noarch.rpm` - the version number in
that filename has changed before, check https://zfsonlinux.org/epel/ if it 404s) and
installs `zfs-dkms`, built against the running kernel.

### 2. Create the ZFS pool

```bash
/usr/libexec/nspawn-vault/init-pool.sh /dev/vdb
```

Destructive - wipes the target device. Requires typing `ja` to confirm. Pool name
defaults to `vault`; pass a second argument to override.

### 3. Install nspawn-vault

```bash
dnf install nspawn-vault-<version>.<dist>.noarch.rpm
```

Installs to `/usr/libexec/nspawn-vault/` (scripts), `/etc/nspawn-vault/` (config),
`/usr/lib/systemd/system/` (timers), `/var/lib/nspawn-vault/state/` (pull status).
Timers are installed but **disabled** - AlmaLinux's default preset policy
(`disable *`) means nothing auto-starts; enable explicitly in step 6.

### 4. Generate the vault's SSH key

```bash
ssh-keygen -t ed25519 -f /root/.ssh/nspawn-vault -N ""
```

One key per vault, never copied to source hosts.

### 5. Trust the vault's key on each source host

On every nspawn-cockpit host this vault should pull from, append the public key with
a forced, restricted command:

```bash
echo 'restrict,command="/usr/local/lib/nspawn-pull/dispatch.sh" '"$(cat /root/.ssh/nspawn-vault.pub)" \
    >> /root/.ssh/authorized_keys
```

`restrict` + `command=` means this key can only ever run `dispatch.sh`, regardless of
what the vault asks for - `dispatch.sh` itself whitelists the exact commands allowed
(`snapshot-db <name>`, `restore-after-backup <name>`, and the rsync read-only
transfer). Multiple vaults can each have their own line without disturbing others.

### 6. Configure and enable

```bash
# List containers to pull from this host, one per line
mkdir -p /etc/nspawn-vault/<source-host>/
echo "<container-name>" >> /etc/nspawn-vault/<source-host>/containers

# Notification credentials for the dead-man's switch (optional but recommended)
cp /etc/nspawn-vault/notify.conf.example /etc/nspawn-vault/notify.conf
chmod 600 /etc/nspawn-vault/notify.conf
# fill in PUSHOVER_TOKEN / PUSHOVER_USER or SLACK_URL

# Enable the always-on timers
systemctl enable --now nspawn-vault-check.timer nspawn-vault-prune.timer

# Enable one templated pull timer per source host
systemctl enable --now "nspawn-vault-pull@<source-host>.timer"
```

### 7. Verify

```bash
systemctl start "nspawn-vault-pull@<source-host>.service"
journalctl -u "nspawn-vault-pull@<source-host>.service" -f
zfs list -t snapshot -r vault
cat /var/lib/nspawn-vault/state/*.json
```

A successful pull writes `{"result":"success","ts":"...","snap":"vault/<host>/<name>@<timestamp>"}`
per container to `/var/lib/nspawn-vault/state/`.

## Retention (GFS)

`nspawn-vault-prune.timer` runs `prune-all.sh` daily at 04:00, applying
Grandfather-Father-Son retention across every dataset. Defaults (24 hourly / 7 daily /
4 weekly / 12 monthly / 3 yearly) live in `/etc/nspawn-vault/gfs.conf` - copy
`gfs.conf.example` to override.

## Database consistency

Source-side config at `/etc/cockpit-nspawn/pull/<container>.conf` controls how the
source host handles databases before each pull - `DB_TYPE=mariadb|postgres` for a
live dump, or `STOP_DURING_BACKUP=true` to stop the container for the duration of the
pull. See cockpit-nspawn's own docs for the source-side half of this.

## Building the RPM

```bash
# Source lives at /root/nspawn-vault/engine/, but %setup in the spec expects
# the tarball's top-level directory to be named "nspawn-vault" - --transform
# renames it during archiving without needing to physically relocate anything.
tar czf nspawn-vault.tar.gz \
    --transform 's,^engine,nspawn-vault,' \
    -C /root/nspawn-vault engine
rpmbuild -bb nspawn-vault.spec
```

## Web UI

A standalone web dashboard (not a Cockpit module) lives alongside this project
at `../web/` - see `../web/README.md`. It shows pull status per host/container,
the same dead-man's-switch staleness this project's `check-stale.sh` computes,
and lets admins edit GFS retention / notification settings / source hosts
without touching config files by hand. Built and verified 2026-07-02.

## Known open items

- ZFS repo/package names for EL10 were only confirmed working 2026-07-01 - re-verify
  if `setup-zfs.sh` starts failing on a future AlmaLinux/OpenZFS release
- Multi-vault replication (`zfs send`/`receive` via `syncoid`) is a future
  consideration, not implemented
- RPM builds also run through an internal CI pipeline now (see `CLAUDE.md`),
  but the manual `rpmbuild` steps above remain the standalone source of truth
