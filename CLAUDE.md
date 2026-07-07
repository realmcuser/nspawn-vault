# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Structure

- `engine/` — pull-backup RPM (`nspawn-vault.spec`): bash/python scripts, systemd units.
- `web/` — standalone web UI RPM (`nspawn-vault-web.spec`): FastAPI backend + React frontend.
- `zfs-bootstrap/` — `nspawn-vault-zfs-bootstrap.spec`: one script
  (`nspawn-vault-setup-zfs`), no dependencies. See "ZFS bootstrap chicken-and-egg"
  below for why this had to be split into its own package.

Two separate RPMs (`web` `Requires: nspawn-vault`), one shared repo — they're
developed in lockstep, most features touch both sides at once. `zfs-bootstrap`
is a third, much smaller package that only exists to solve one dependency
ordering problem — it's not developed in lockstep with the other two.

## Commands

There is no broad automated test suite in this repo (neither `engine/` nor
`web/`) — verification is manual/end-to-end against the real VM, see
"Staleness logic" below for the one documented cross-check procedure. The
one exception: `web/backend/test_vault_archive.py` (stdlib `unittest`, no
pytest — this file also feeds the RPM's shipped runtime venv, see gotcha
#4/#5, so no test-only dependency belongs in `requirements.txt`), a
regression test for `vault_archive.resolve_safe_path`'s path-traversal/
symlink-escape protection — the one place the file browser/single-file
download endpoints resolve an arbitrary user-supplied path against a
container's own filesystem, which can contain symlinks pointing anywhere.
Run with `python3 -m unittest test_vault_archive -v` from `web/backend/`.
Confirmed live (2026-07-07) that this test suite actually fails if the
safety check is naively removed — 6 of 12 tests catch it.

**Web backend (dev server):**
```bash
cd web/backend
python3 -m venv venv && venv/bin/pip install -r requirements.txt
SECRET_KEY=dev-secret DATABASE_URL=sqlite:///./dev.db venv/bin/uvicorn main:app --reload
```

**Web frontend (dev server):**
```bash
cd web/frontend
npm install
VITE_API_URL=http://127.0.0.1:8000 npm run dev   # Vite, defaults to :5173
npm run lint                                      # eslint .
npm run build                                     # produces frontend/dist/, required before packaging
```
`main.py`'s CORS middleware hardcodes `allow_origins=["http://localhost:5173"]`
— if the Vite dev port ever changes, update that list too or the dev frontend
silently gets CORS errors talking to a `--reload` backend.

**Building the engine RPM** (`engine/nspawn-vault.spec`):
```bash
tar czf nspawn-vault.tar.gz --transform 's,^engine,nspawn-vault,' -C /root/nspawn-vault engine
rpmbuild -bb nspawn-vault.spec
```

**Building the web RPM** (`web/nspawn-vault-web.spec`) — build the frontend first,
the spec only packages the pre-built `dist/`, it does not run `npm` itself:
```bash
cd web/frontend && npm install && npm run build
cd ..
tar czf nspawn-vault-web.tar.gz \
    --exclude='web/backend/venv' --exclude='web/frontend/node_modules' \
    --transform 's,^web,nspawn-vault-web,' -C /root/nspawn-vault web
rpmbuild -bb nspawn-vault-web.spec
```
See gotcha #4 below before running this anywhere but the actual target distro.

## Architecture

Three tiers, only the last two live in this repo:

1. **Source host** (nspawn-cockpit, not this repo): `dispatch.sh` is a forced
   SSH command (`restrict,command=...` in `authorized_keys`) that whitelists
   exactly `snapshot-db <name>`, `restore-after-backup <name>`, and a
   read-only rsync (`rrsync -ro`). The source host never holds a credential
   that can reach the vault — the vault always initiates.
2. **Engine** (`engine/`, installs to `/usr/libexec/nspawn-vault/`): `pull.sh`
   pulls one container over SSH, rsyncs it read-only, takes a ZFS snapshot,
   and writes `/var/lib/nspawn-vault/state/vault_<host>_<name>.json`
   (`{"result": "success"|"failed", "ts": ..., "snap": ...}`). `pull-host.sh`
   loops `pull.sh` over every container listed in
   `/etc/nspawn-vault/<host>/containers`. Three systemd timers drive
   everything: `nspawn-vault-pull@<host>.timer` (templated, one instance per
   source host), `nspawn-vault-check.timer` → `check-stale.sh` (dead-man's
   switch, every 30 min), `nspawn-vault-prune.timer` → `prune-all.sh` /
   `gfs-prune.sh` / `gfs.py` (GFS retention, daily 04:00).
3. **Web** (`web/`): FastAPI backend + React frontend, runs directly on the
   vault host. It reads the same `/var/lib/nspawn-vault/state/*.json` files
   and `/etc/nspawn-vault/` config the engine writes, and shells out locally
   to `zfs`/`systemctl` — **it never SSHes anywhere itself**; triggering an
   actual pull/prune is always the engine's timers, not the web app.

Backend module split (`web/backend/`):
- `main.py` wires the two routers and serves the built frontend as a static
  fallback (Caddy normally does this in production, see `web/Caddyfile`).
- `auth_routes.py` / `auth_utils.py` / `database.py` / `models.py` /
  `ldap_service.py` — the auth stack, see "Auth" below.
- `vault_config.py` — validates and reads/writes `/etc/nspawn-vault/*`
  (host/container lists, `gfs.conf`, `notify.conf`); owns the hostname/
  container-name validation regexes and the notify.conf shell-quoting.
- `vault_zfs.py` — shells out to `zfs list`/`zfs get`, parses dataset names
  under `vault/<host>/<container>`; also `module_status()`, which checks
  `lsmod`/`dkms status -k $(uname -r)` to detect the zfs kernel module not
  being loaded for the *currently running* kernel (see gotcha #7) — folded
  into `GET /api/alerts/summary` as `zfs_module_status`.
- `vault_systemd.py` — shells out to `systemctl`/`date` for timer status and
  next-run time; owns the `NextElapseUSecRealtime` parsing gotcha (#3 below).
  Also `fetch_pull_log()`, which reads the `nspawn-vault-pull@<host>.service`
  journal (the unit's `StandardOutput`/`StandardError=journal`) windowed
  around one pull's `ts`, since the state JSON's own `msg` field (from
  pull.sh's `fail()`) is only ever a short canned string, never the real
  rsync/ssh/zfs error text.
- `vault_ssh.py` — `test_connection(host)`, an ad-hoc SSH reachability
  check against a pull source host (not tied to an existing config entry,
  so it works both before and after a host is added). Since every source
  host forces the session through its own `dispatch.sh`, our probe command
  is always rejected — the check instead relies on ssh's own exit code
  (255 = transport/auth failure, anything else = the key was accepted and
  the forced command ran) to distinguish "can't reach this host at all"
  from "reached it, dispatch.sh just doesn't like our probe."
- `vault_state.py` — the Python port of `check-stale.sh`'s staleness logic
  (see dedicated section below).
- `vault_routes.py` — ties the above together into the `/api/hosts*`
  (including `GET .../containers/{container}/log`, the detailed-failure-log
  endpoint), `/api/admin/settings/*`, `/api/admin/hosts*` (including `POST
  .../test-connection`), `/api/alerts/summary` endpoints; admin-only
  mutations are gated with `Depends(get_current_admin)`.

Frontend (`web/frontend/src/`): `pages/{Login,Dashboard,HostDetail,Admin}.jsx`
are the four screens; `context/AuthContext.jsx` holds the JWT and full user
record (including `role`, used to gate the admin UI) and calls
`fetchCurrentUser()` after login rather than trusting the login response
shape; `services/api.js` is the HTTP client; `components/StaleAlertBanner.jsx`
is the deliberately loud (`bg-red-600`, large icon) staleness banner —
intentionally escalated past a typical subtle badge, per product decision.
`components/ZfsAlertBanner.jsx` reuses the exact same loud styling for a
different alert: the zfs kernel module not being loaded for the running
kernel, which breaks *every* host's pulls at once, not just one — Dashboard
also shows a persistent (non-alert) ZFS module status card at all times, not
just when broken, per explicit request after the 2026-07-02 dkms incident.
`components/Modal.jsx` is the one generic modal (backdrop, Escape-to-close),
used by `HostDetail.jsx`'s "view log" button on failed containers (renders
`vault_systemd.fetch_pull_log()`'s journal excerpt in a `<pre>` block) and
by `Admin.jsx`'s per-host and add-host-form "test connection" buttons
(call `vault_ssh.test_connection()`, inline result box like the existing
LDAP test-connection pattern rather than a modal there).
`locales/{en,sv}/translation.json` back `i18n.js` (react-i18next).

## Deployment target

Build/test VM: AlmaLinux 10.2, `198.51.100.20` (root SSH). Caddy on `:80`
(plain `http://`, no TLS — LAN-only, WAN access not required) reverse-proxies
`/api/*` to uvicorn on `127.0.0.1:8000` and serves the built frontend directly.

Production vault is still Ubuntu — migration to this AlmaLinux stack is
in progress, not yet cut over.

## Critical gotchas (already hit once each — don't repeat)

1. **`check-stale.sh` sources `notify.conf` as root.** Any write path into
   that file MUST shell-quote values (`web/backend/vault_config.py`'s
   `_shell_quote()`). An unescaped `$()` in a Pushover token would execute
   as root next time the dead-man's-switch timer fires. Verified with a live
   injection test (`$(touch /tmp/PWNED)`) — confirm it stays inert if this
   code changes.
2. **Host/container names become filesystem paths and ZFS dataset segments.**
   Always validate against `_HOSTNAME_RE` / `_CONTAINER_NAME_RE` in
   `vault_config.py` before touching disk — a hostname of `../../etc/evil`
   must be rejected, not silently accepted.
3. **`date -d ""` does not fail.** It silently parses to "today" instead of
   erroring. Any `NextElapseUSecRealtime`-style shell snippet (see
   `vault_systemd.py`, and the original in `/root/nspawn-cockpit/src/BackupsOverview.jsx`)
   must explicitly check for an empty string before calling `date -d`, or a
   missing/unscheduled timer reports a bogus-but-valid epoch instead of `None`.
4. **RPM builds must run on a host matching the target distro's Python
   version.** `web/nspawn-vault-web.spec` vendors a venv via `pip install` —
   compiled wheels (argon2-cffi, cryptography) are ABI-tied to the build
   Python. Building on Fedora 44 (3.14) and installing on AlmaLinux 10 (3.12)
   breaks the venv outright. Always build inside the actual target (or an
   equivalent mock/container), never assume dev-host Python == target Python.
5. **Vendored venvs are not relocatable.** `python3 -m venv`'s console-script
   wrappers (`venv/bin/uvicorn`, `venv/bin/pip`) bake the build-time absolute
   path into their shebang. The systemd unit invokes `venv/bin/python3 -m
   uvicorn ...` (not the wrapper script) to sidestep this entirely; `%install`
   also rewrites shebangs, and `%__requires_exclude_from`/
   `%__provides_exclude_from` excludes the venv from RPM's automatic
   dependency scan (which otherwise leaks the build host's temp path in as a
   bogus `Requires`).
6. **`%global debug_package %{nil}`** is required in `nspawn-vault-web.spec` —
   RPM's debuginfo generator fails the build on vendored wheels with no
   extractable debug symbols.
7. **DKMS autobuild is not reliable when `kernel-devel` and `zfs`/`zfs-dkms`
   install in the same `dnf` transaction.** `nspawn-vault-setup-zfs` runs
   `dnf install -y kernel-devel-"$(uname -r)" zfs` as one command — RPM's
   scriptlet ordering across packages in a single transaction isn't
   guaranteed, so `zfs-dkms`'s own `%post` autobuild trigger can run before
   `kernel-devel` is actually unpacked on disk. Result: `dkms status` shows
   `zfs/x.y.z: added` (registered) but never `installed` (built), and the
   following `modprobe zfs` fails with "Module zfs not found in directory
   /lib/modules/...". Hit live 2026-07-02 on a fresh AlmaLinux 10.2 install
   (192.0.2.11). Fixed by having the script explicitly check `dkms status`
   for `: installed` after the dnf transaction and run `dkms install -m zfs
   -v "$zfs_ver" -k "$kver"` itself if it's missing — never assume the RPM
   trigger ran just because the dnf transaction succeeded.

## Auth (`web/backend`)

Ported almost verbatim from an existing internal admin tool's auth stack:
JWT + argon2, local-then-LDAP fallback, first-user-becomes-admin bootstrap.
Two deliberate deviations from that original:

- **SQLite + WAL, not Postgres** — write volume for a handful of admin
  accounts doesn't justify a second stateful service on the vault host.
- **LDAP admin-group membership is re-checked on every login**, not just at
  first auto-provisioning (the original only checks once — an admin removed
  from the LDAP admin group stays admin there forever until manually fixed).

Secrets (LDAP `bind_password`, Pushover token, Slack URL) are never returned
in plaintext by `GET` endpoints — always a `"********"` sentinel when a value
is set, and the corresponding `PUT` preserves the stored value unless the
field actually changed. Follow this same pattern for any new secret field.

## ZFS bootstrap chicken-and-egg

`engine/nspawn-vault.spec` declares `Requires: zfs`, but ZFS is not in
AlmaLinux/EL's own repos (CDDL/GPL license conflict) — dnf can't resolve that
dependency until the OpenZFS repo has been added and `zfs`/`zfs-dkms`
installed. The script that does that (`nspawn-vault-setup-zfs`, née
`engine/scripts/setup-zfs.sh`) can therefore **never** live inside the
`nspawn-vault` RPM itself: dnf would refuse to even install a package
containing it, since its `Requires: zfs` is already unresolvable at that
point. Learned this the hard way when asked "won't the script just be
inaccessible if the RPM that contains it can't install?" — yes, exactly.

Fixed by splitting it into `zfs-bootstrap/` → `nspawn-vault-zfs-bootstrap`
RPM, which carries **no dependencies at all**, so dnf can always install it
first regardless of what repos exist yet. Real install order for a fresh
host:
```bash
dnf install nspawn-vault-zfs-bootstrap-<version>.<dist>.noarch.rpm
nspawn-vault-setup-zfs          # idempotent, no-ops if zfs already present
dnf install nspawn-vault-<version>.<dist>.noarch.rpm
```
`init-pool.sh` (creates the zpool) has no such problem and stays inside the
main `nspawn-vault` package — it only runs *after* both zfs and nspawn-vault
are already installed, no ordering conflict.

## Staleness logic must match `check-stale.sh` exactly

`web/backend/vault_state.py`'s `compute_status()` is a Python port of
`engine/usr/libexec/nspawn-vault/check-stale.sh`'s alert logic
(`THRESHOLD_MIN=180`). If either file changes, re-run the cross-check: run
`check-stale.sh` manually on the vault and diff its OK/ALERT verdicts against
`GET /api/hosts/<host>` for the same containers at the same moment. They must
agree exactly — the CLI dead-man's-switch and the web UI must never disagree
about what counts as stale.

## Building via an internal CI pipeline

All three packages (`nspawn-vault`, `nspawn-vault-web`,
`nspawn-vault-zfs-bootstrap`) are also built through an internal CI/build
pipeline that isn't part of this repo. It's not required to build this
project — the manual `tar`/`rpmbuild` commands documented in
`engine/README.md`, `web/README.md`, and `zfs-bootstrap/README.md` are the
actual source of truth and work completely standalone; the pipeline just
automates the same steps remotely (fetch the source tree, run the same tar
recipe, `rpmbuild` inside a container matching each target distro).

`nspawn-vault` and `nspawn-vault-zfs-bootstrap` build for several EL/Fedora
targets since they're pure noarch scripts with no compiled anything.
`nspawn-vault-web` is deliberately restricted to a single target distro
(currently AlmaLinux 10) instead of mirroring that spread: it vendors a
compiled-wheel Python venv (gotcha #4/#5), so every additional target
distro needs to be an actual deployment target to be worth building for -
add more here deliberately if a second one is ever needed, don't just copy
the other projects' distro list.

**Gotcha if this repo is ever wired into a similar "raw spec" CI tool
again**: some such tools store their own copy of a `.spec` file's contents
rather than reading it live from the repo on each build. If so, that stored
copy needs to be explicitly re-synced any time `engine/nspawn-vault.spec`,
`web/nspawn-vault-web.spec`, or `zfs-bootstrap/nspawn-vault-zfs-bootstrap.spec`
changes - there is no automatic sync in general, and a stale stored copy
will silently build an outdated package with no error. Hit this live once
already; don't assume a new CI setup handles it for you.
