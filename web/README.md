# nspawn-vault-web

Standalone (non-Cockpit) web dashboard for [nspawn-vault](https://github.com/realmcuser/nspawn-vault),
the pull-based backup engine for cockpit-nspawn hosts. Shows per-host/per-container
backup status, the same dead-man's-switch staleness `check-stale.sh` already
computes, and an unmissable red banner when something's gone stale. Local +
LDAP login, modeled on the build tool's auth stack.

Runs directly on the vault host (reads `zfs list`/`systemctl` output locally,
never SSHes anywhere itself - that's nspawn-vault's job).

## Stack

- Backend: FastAPI + SQLAlchemy + SQLite (WAL mode) + python-jose (JWT) +
  passlib/argon2 + ldap3
- Frontend: React 19 + Vite + Tailwind (same palette as the internal build tool) + react-router-dom + i18next (en/sv)

## Local development

```bash
cd backend
python3 -m venv venv && venv/bin/pip install -r requirements.txt
SECRET_KEY=dev-secret DATABASE_URL=sqlite:///./dev.db venv/bin/uvicorn main:app --reload

cd ../frontend
npm install
VITE_API_URL=http://127.0.0.1:8000 npm run dev
```

## Building the RPM

```bash
cd frontend && npm install && npm run build   # produces frontend/dist/
cd ..
# Source lives at /root/nspawn-vault/web/, but %setup in the spec expects the
# tarball's top-level directory to be named "nspawn-vault-web" - --transform
# renames it during archiving without needing to physically relocate anything.
tar czf nspawn-vault-web.tar.gz \
    --exclude='web/backend/venv' --exclude='web/frontend/node_modules' \
    --transform 's,^web,nspawn-vault-web,' \
    -C /root/nspawn-vault web
rpmbuild -bb nspawn-vault-web.spec
```

**Build on a host matching the target distro's Python version.** The spec
vendors a venv via `pip install` at build time - if you build on, say, Fedora
44 (Python 3.14) and install on AlmaLinux 10 (Python 3.12), the venv's
`lib/python3.14/site-packages/` won't even be found by the target's
`/usr/bin/python3`, and every compiled wheel (argon2-cffi, cryptography) is
ABI-tied to the build Python anyway. Build inside the actual target distro
(or an equivalent mock/container) - this is why the internal build tool builds each distro
target separately rather than once for everyone.

### Two packaging gotchas already hit and fixed here

1. **Empty debuginfo package.** `BuildArch: x86_64` (needed because the venv
   has compiled C extensions) makes RPM try to generate a `-debuginfo`/
   `-debugsource` subpackage. There's nothing useful to extract from vendored
   third-party wheels, so this fails the build with "Empty %files file
   debugsourcefiles.list" unless disabled via `%global debug_package %{nil}`.
2. **venv is not relocatable.** `python3 -m venv` bakes an absolute path into
   every console-script wrapper's shebang (`venv/bin/uvicorn`, `venv/bin/pip`,
   ...) at `pip install` time. Copying the venv into `%{buildroot}` after the
   fact leaves those shebangs pointing at the build host's temp directory -
   RPM's dependency scanner even picks this up as a bogus `Requires` on a
   path that only exists on the machine that built the package. Fixed two
   ways: the systemd unit invokes `venv/bin/python3 -m uvicorn ...` instead
   of the wrapper script (python3 itself is a plain symlink, not a scripted
   shebang, so it's location-independent), and `%install` rewrites every
   shebang under `venv/bin/` to the real install path as a belt-and-suspenders
   fix for anyone invoking those scripts by hand. `%__requires_exclude_from`/
   `%__provides_exclude_from` also exclude the vendored venv from RPM's
   automatic dependency scanning entirely.

Verified end-to-end 2026-07-01/02 on a real AlmaLinux 10.2 test VM: built via
`rpmbuild` on that VM (matching Python 3.12), installed with `dnf install`,
service started clean, served real ZFS-backed data through the packaged path.
Caddy (from EPEL, already enabled on this VM) verified too: `Caddyfile`
reverse-proxies `/api/*` to `127.0.0.1:8000` and serves `frontend/dist/`
directly on `:80`, confirmed reachable over the LAN while uvicorn itself
stays bound to `127.0.0.1` only - Caddy is the only thing exposed to the
network. The example Caddyfile uses `http://` (plain HTTP, no ACME/TLS
attempt) since this is meant for LAN-only access; swap in a real domain and
drop the `http://` prefix if WAN + automatic HTTPS is ever needed.

## First-run setup

```bash
cp /etc/nspawn-vault-web/env.example /etc/nspawn-vault-web/env
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" /etc/nspawn-vault-web/env
chmod 600 /etc/nspawn-vault-web/env
systemctl enable --now nspawn-vault-web.service
```

Caddy is **not installed or configured by this package** - two things a fresh
host is missing by default, both hit live 2026-07-02/03 on a real install
(192.0.2.11):

```bash
dnf install caddy   # if not already installed
cp /usr/share/doc/nspawn-vault-web/Caddyfile /etc/caddy/Caddyfile
systemctl enable --now caddy   # or: systemctl reload caddy if already running

# firewalld blocks port 80 by default on a fresh AlmaLinux install - open it:
firewall-cmd --add-service=http --permanent && firewall-cmd --reload
```

Caddy's own **stock** `/etc/caddy/Caddyfile` only serves its built-in welcome
page - it will NOT reverse-proxy to this app until overwritten with the one
this package ships (`%{_docdir}/nspawn-vault-web/Caddyfile` /
`/usr/share/doc/nspawn-vault-web/Caddyfile`). If the site isn't reachable
after install, check these two things (`systemctl status caddy`,
`firewall-cmd --list-services`, `cat /etc/caddy/Caddyfile`) before suspecting
SELinux - Caddy runs as `unconfined_service_t` on AlmaLinux/EL out of the
box (`ps -Z -C caddy`), so it's rarely the actual cause; confirm with
`ausearch -m avc -ts recent` before disabling it.

Once both are in place, visit the site and register the first account - it
becomes admin automatically.

## Connecting a new source host

This app's Admin page only manages **configuration and connectivity
testing** for a source host - it never SSHes anywhere itself (see the
Architecture section in the top-level `CLAUDE.md`: only `nspawn-vault`'s own
engine timers do that). Adding a host from the web UI does exactly two
things:

1. Writes `/etc/nspawn-vault/<host>/containers` (which containers to pull) -
   `POST /api/admin/hosts`.
2. Lets you click **Test connection**
   (`POST /api/admin/hosts/test-connection`) to confirm the vault can
   actually reach that host over SSH with its own key - before or after
   adding it. Since it takes a bare hostname rather than a saved config
   entry, this works both while typing a new host into the "Add host" form
   and later against one already configured.

That's the *vault* side, and it's the only side this app can ever touch.
It is **not enough on its own** - the source host itself needs three things
installed before a pull can succeed, none of which this app can do for you:

- `dispatch.sh` + `snapshot-db.sh` + `restore-after-backup.sh` at
  `/usr/local/lib/nspawn-pull/` on the source host - the forced-command
  dispatcher. See `../source-host/README.md` in this repo for what each one
  does and how they were verified.
- A `restrict,command="/usr/local/lib/nspawn-pull/dispatch.sh"` line in the
  source host's `/root/.ssh/authorized_keys`, carrying *this vault's* public
  key (`/root/.ssh/nspawn-vault.pub` on the vault host). There's no "copy
  public key" button in this UI yet - copy it by hand for now.
- Optionally, `/etc/cockpit-nspawn/pull/<container>.cnf` on the source host,
  only if that container has a database to dump.

As of 2026-07, that source-host setup is **entirely manual** -
`source-host/` in this repo is a tested reference implementation you copy
over by hand (see its README for the exact steps). "Test connection" only
tells you whether that manual setup has already succeeded; it cannot
perform the setup itself, since this app only ever runs on the vault.

The intended end state - not built yet, see `PULL-BACKUP-INTEGRATION.md` in
the `nspawn-cockpit` repo - is an "Enable pull backup" toggle **inside
cockpit-nspawn itself**, since that's the tool that already runs directly on
the source host with root access. That toggle would perform the three
bullets above for you, reusing the exact same three scripts from
`source-host/` in this repo rather than reimplementing their logic
independently. Until that toggle exists, the flow for a new source host is:

1. Manually install `source-host/`'s scripts and `authorized_keys` line on
   the new source host (`source-host/README.md`).
2. Add the host and its containers here, in this app's Admin page.
3. Click **Test connection** to confirm the SSH side actually works.
4. Enable the pull timer for that host.
