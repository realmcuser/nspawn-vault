# nspawn-vault

nspawn-vault is a backup system for [cockpit-nspawn](https://github.com/realmcuser/cockpit-nspawn)
hosts — machines that run one or more [systemd-nspawn](https://www.freedesktop.org/software/systemd/man/systemd-nspawn.html)
containers, managed through a Cockpit web UI. Instead of each of those hosts
pushing its own backups out somewhere, a separate, dedicated **vault**
machine reaches out and *pulls* a read-only copy of every configured
container on a schedule, snapshots it with ZFS, and keeps a rolling
retention history.

This repo contains two things that ship as separate RPMs but are developed
together, since most changes touch both:

- **[`engine/`](engine/README.md)** (package `nspawn-vault`) — the backup
  engine itself. Systemd timers that SSH out to each source host, pull a
  container over rsync, take a ZFS snapshot, apply GFS retention, and raise
  a dead-man's-switch alert if a host stops producing fresh backups.
- **[`web/`](web/README.md)** (package `nspawn-vault-web`) — a web
  dashboard for the engine. Shows pull status per host and container, an
  unmissable banner when something's gone stale, and an admin page for
  configuring source hosts, retention, and notifications.

## Why pull, not push

The obvious way to back up a fleet of containers is to have each host push
its own data out to wherever backups live. The problem: pushing means each
host holds a credential that can *reach and write to* the backup storage.
If that host is ever compromised, whoever's on it can use that same
credential to reach the backups too — and delete or encrypt them right
alongside the production data they were supposed to protect.

nspawn-vault inverts this. The vault is the only side that holds a
credential, and that credential can only ever *read* from a source host,
never write to it — a source host cannot reach the vault, cannot trigger a
backup, and cannot touch anything the vault has already stored. A
compromised container host can, at worst, destroy itself; it can't take its
own backup history down with it. The vault also keeps its ZFS snapshots
read-only and unreachable from the source side, so even a compromised vault
credential (the SSH key it uses to reach a source host) can only read that
one host — never modify or delete a snapshot that already exists.

See [`pull-backup-threat-model.md`](pull-backup-threat-model.md) for the
full design reasoning this was built from.

## Why a standalone web app, not a Cockpit plugin

This was originally planned as a Cockpit module (`cockpit-nspawn-vault`),
but ended up as a fully standalone web app instead, for two reasons:

1. **Cleaner alerting.** The vault needs to show unmissable warnings the
   moment a source host's backups stop coming in — a large red banner, not
   a subtle badge. Building that inside Cockpit's own UI framework means
   working within its constraints; a standalone frontend can just do
   whatever the alert needs to look like.
2. **No Cockpit dependency.** The vault is already a separate machine with
   its own job — receive pull-backups, manage ZFS, raise alerts — and none
   of that needs `machinectl` or any of Cockpit's own container-management
   machinery. There's nothing Cockpit-shaped for the vault to plug into.

So `nspawn-vault-web` is a small FastAPI + React app that runs directly on
the vault, with its own login and admin accounts (local or LDAP) rather
than relying on Cockpit/PAM. It reads the same state the engine already
writes and shells out locally to `zfs` / `systemctl` — it never SSHes
anywhere itself, which is deliberate: the engine keeps sole ownership of
every credential that can reach a source host.

## Platform

The vault runs on **AlmaLinux 10**. Both packages are built and tested
against that target specifically, and `nspawn-vault`'s `Requires: zfs`
depends on the OpenZFS repo being set up for it (see
[`zfs-bootstrap/`](zfs-bootstrap/README.md)) — pick AlmaLinux 10 for the
vault host and don't worry about anything past that.

## Further reading

- [`design.md`](design.md) and [`pull-backup-threat-model.md`](pull-backup-threat-model.md) —
  full design history and threat model.
- [`CLAUDE.md`](CLAUDE.md) — operational gotchas worth knowing before
  touching this code.
