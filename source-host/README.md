# Source-host scripts (draft)

These three scripts implement the *source-host* side of the pull-backup
design in `../pull-backup-threat-model.md` (section 3). They do **not**
belong to either RPM in this repo (`nspawn-vault` / `nspawn-vault-web`) -
they run on the customer/nspawn-cockpit host being backed up, not on the
vault. `engine/README.md`'s "Trust the vault's key" step and `../CLAUDE.md`
reference this path; this directory is where that reference actually lives.

This is a first draft, manually installed - a toggle in cockpit-nspawn
itself to automate this is future work (see `pull-backup-threat-model.md`
section 7), not built yet.

## What each file does

- **`dispatch.sh`** - the forced SSH command itself. Whitelists exactly
  `snapshot-db <name>`, `restore-after-backup <name>`, and a read-only
  rsync of one container's live tree under `/var/lib/machines/<name>/`.
  This is the actual access-control boundary - read it before changing
  anything else here.
- **`snapshot-db.sh`** - best-effort application-consistent DB dump, run
  *inside* the container via `systemd-run --machine=`, so the DB password
  never has to leave this host. No-ops if the container has no DB
  credentials file configured (see `example.cnf`) - that's a normal case,
  not an error.
- **`restore-after-backup.sh`** - cleanup, always run by the vault's
  `pull.sh` after the pull whether it succeeded or failed. Removes the
  temporary DB dump `snapshot-db.sh` left inside the container.
- **`example.cnf`** - template for the per-container DB credentials file.

## Install

On the source host, as root:

```bash
mkdir -p /usr/local/lib/nspawn-pull
cp dispatch.sh snapshot-db.sh restore-after-backup.sh /usr/local/lib/nspawn-pull/
chmod 755 /usr/local/lib/nspawn-pull/dispatch.sh \
          /usr/local/lib/nspawn-pull/snapshot-db.sh \
          /usr/local/lib/nspawn-pull/restore-after-backup.sh

# Trust the vault's key, forced through dispatch.sh - replace with the
# real public key from /root/.ssh/nspawn-vault.pub on the vault:
echo 'restrict,command="/usr/local/lib/nspawn-pull/dispatch.sh" ssh-ed25519 AAAA... root@vault.example.com' \
    >> /root/.ssh/authorized_keys

# Only if this container has a DB to dump:
mkdir -p /etc/cockpit-nspawn/pull
cp example.cnf "/etc/cockpit-nspawn/pull/<container-name>.cnf"
chmod 600 "/etc/cockpit-nspawn/pull/<container-name>.cnf"
# edit user=/password= to match a real DB account with dump privileges
```

`rrsync` ships with the `rsync` package itself - if `which rrsync` comes up
empty, it's usually at `/usr/share/doc/rsync/support/rrsync` (copy/symlink
it onto `$PATH`, e.g. `/usr/local/bin/rrsync`).

## Known limitations (draft)

- No filesystem-level snapshot of the container's tree - rsync reads the
  *live* directory (this matches the "start simple" recommendation in
  `pull-backup-threat-model.md` 3.3; a btrfs subvolume snapshot as a source
  for rrsync is the suggested next step if a container's files themselves
  need atomic consistency beyond what `snapshot-db.sh`'s DB dump already
  gives the database contents).
- No locking against two concurrent pulls of the *same* container (e.g. two
  vaults both configured to pull the same host) - both would run
  `snapshot-db.sh`/`mysqldump` concurrently into the same dump file path.
  Fine for the current single-vault deployment, worth revisiting before a
  second vault is pointed at the same source host.
- `dispatch.sh`'s `NAME_RE` must stay in sync with
  `web/backend/vault_config.py`'s `_CONTAINER_NAME_RE` - they're the two
  ends of the same trust boundary, drifting apart would either reject valid
  names or (worse) accept something the other side wouldn't have.
