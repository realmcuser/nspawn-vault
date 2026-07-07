Name:           nspawn-vault-web
Version:        0.1.0
Release:        1%{?dist}
Summary:        Standalone web UI for nspawn-vault
License:        LGPL-2.1-or-later
BuildArch:      x86_64
Source0:        nspawn-vault-web.tar.gz

# Vendored venv contains compiled third-party wheels (argon2-cffi, etc.) with
# no useful debug symbols to extract - disable RPM's automatic debuginfo
# generation, which otherwise fails the build with an empty debugsourcefiles.list.
%global debug_package %{nil}

# Don't let RPM's dependency scanner walk the vendored venv: it picks up
# console-script wrapper shebangs (uvicorn, pip, ...) as literal file-path
# Requires, which reference the build host's temp path and don't exist on
# any installed system. The shebangs themselves are still fixed below in
# %%install so the wrapper scripts work if invoked manually.
%global __requires_exclude_from ^%{_datadir}/nspawn-vault-web/venv/.*$
%global __provides_exclude_from ^%{_datadir}/nspawn-vault-web/venv/.*$

%{?systemd_requires}
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3

Requires:       python3
Requires:       systemd
Requires:       zfs
Requires:       nspawn-vault
Requires:       zstd

%description
Standalone (non-Cockpit) web dashboard for nspawn-vault: shows backup
status per source host/container, dead-man's-switch staleness (same
definition as check-stale.sh), and admin-managed local/LDAP login.
Runs as a systemd service (uvicorn on 127.0.0.1), reverse-proxied
externally by Caddy. Read-only against nspawn-vault's state - never
writes to /etc/nspawn-vault or triggers pulls/prunes itself.

The frontend must be built (npm run build) BEFORE packaging - this spec
installs the pre-built frontend/dist/, it does not run npm itself.

After install:
  1. cp %{_sysconfdir}/nspawn-vault-web/env.example %{_sysconfdir}/nspawn-vault-web/env
     (fill in a random SECRET_KEY, chmod 600)
  2. systemctl enable --now nspawn-vault-web.service
  3. Point Caddy at 127.0.0.1:8000 (see %{_docdir}/nspawn-vault-web/Caddyfile)
  4. Visit the site, register the first account (becomes admin automatically)

%prep
%setup -q -n nspawn-vault-web

%build
python3 -m venv %{_builddir}/nspawn-vault-web-build/venv
%{_builddir}/nspawn-vault-web-build/venv/bin/pip install --no-cache-dir -r backend/requirements.txt

%install
install -d %{buildroot}%{_datadir}/nspawn-vault-web/backend
cp -p backend/*.py %{buildroot}%{_datadir}/nspawn-vault-web/backend/
# test_*.py (see test_vault_archive.py) is dev-only - runs against the
# source tree during development, has no business in the shipped package
rm -f %{buildroot}%{_datadir}/nspawn-vault-web/backend/test_*.py

install -d %{buildroot}%{_datadir}/nspawn-vault-web/venv
cp -a %{_builddir}/nspawn-vault-web-build/venv/. %{buildroot}%{_datadir}/nspawn-vault-web/venv/
# venv is not relocatable: console-script wrappers (uvicorn, pip, ...) have
# the build-time venv path baked into their shebang. Rewrite to the real
# install path so they work if invoked directly (the systemd unit itself
# avoids this by calling `venv/bin/python3 -m uvicorn` instead).
grep -rlZ "^#!.*/bin/python3$" %{buildroot}%{_datadir}/nspawn-vault-web/venv/bin/ 2>/dev/null | \
    xargs -0 -r sed -i "1s|^#!.*python3.*$|#!%{_datadir}/nspawn-vault-web/venv/bin/python3|"

install -d %{buildroot}%{_datadir}/nspawn-vault-web/frontend/dist
cp -r frontend/dist/. %{buildroot}%{_datadir}/nspawn-vault-web/frontend/dist/

install -d %{buildroot}%{_sysconfdir}/nspawn-vault-web
install -m 0644 env.example %{buildroot}%{_sysconfdir}/nspawn-vault-web/

install -d %{buildroot}%{_unitdir}
install -m 0644 systemd/nspawn-vault-web.service %{buildroot}%{_unitdir}/

install -d %{buildroot}%{_sharedstatedir}/nspawn-vault-web

install -d %{buildroot}%{_docdir}/nspawn-vault-web
install -m 0644 Caddyfile %{buildroot}%{_docdir}/nspawn-vault-web/

%files
%{_datadir}/nspawn-vault-web
%dir %{_sysconfdir}/nspawn-vault-web
%{_sysconfdir}/nspawn-vault-web/env.example
%{_unitdir}/nspawn-vault-web.service
%dir %{_sharedstatedir}/nspawn-vault-web
%{_docdir}/nspawn-vault-web/Caddyfile

%post
%systemd_post nspawn-vault-web.service
echo ""
echo "nspawn-vault-web installed. Next steps:"
echo "  1. cp %{_sysconfdir}/nspawn-vault-web/env.example %{_sysconfdir}/nspawn-vault-web/env"
echo "     (fill in a random SECRET_KEY, then: chmod 600 %{_sysconfdir}/nspawn-vault-web/env)"
echo "  2. systemctl enable --now nspawn-vault-web.service"
echo "  3. Caddy is NOT installed by this package and does NOT reverse-proxy"
echo "     to this app out of the box - its stock Caddyfile only serves its"
echo "     own welcome page:"
echo "       dnf install caddy   # if not already installed"
echo "       cp %{_docdir}/nspawn-vault-web/Caddyfile /etc/caddy/Caddyfile"
echo "       systemctl enable --now caddy   # or: systemctl reload caddy"
echo "  4. If firewalld is active, port 80 is closed by default - open it:"
echo "       firewall-cmd --add-service=http --permanent && firewall-cmd --reload"
echo "  5. Visit http://<this-host>/ and register the first account - it"
echo "     becomes admin automatically."
echo "  (SELinux enforcing is fine and does not need to be disabled - Caddy"
echo "   runs unconfined by default on AlmaLinux/EL, no AVC denials expected.)"
echo ""

%preun
%systemd_preun nspawn-vault-web.service

%postun
%systemd_postun_with_restart nspawn-vault-web.service

%changelog
* Tue Jul 07 2026 Developer <dev@example.com> - 0.1.0-22
- Fixed LDAP admin-group detection: the memberOf lookup searched the whole
  base_dn subtree by (user_attr=username), which can match more than one
  entry on a directory with a legacy/compat view alongside the real
  accounts tree (e.g. FreeIPA's cn=users,cn=compat,... - present for older
  LDAP clients, has no memberOf populated). entries[0] wasn't guaranteed to
  be the real account, so a genuine admin-group member could have their
  role silently reset to non-admin on every login. Now looks up memberOf
  at the exact DN the user just authenticated with instead of a fresh
  ambiguous search. Confirmed live against a real FreeIPA server.

* Tue Jul 07 2026 Developer <dev@example.com> - 0.1.0-18
- Adds a browse/single-file-download feature: a read-only file browser into
  a chosen snapshot (vault_archive.list_snapshot_dir/resolve_safe_path),
  so recovering one file doesn't require downloading and decompressing the
  whole container archive, and doesn't require shell/SSH access to the
  vault host - which the people actually using this UI may not have or be
  allowed. resolve_safe_path guards against a container's own filesystem
  containing symlinks that point outside the snapshot root (absolute or
  via enough ../.. segments) - covered by a new regression test,
  test_vault_archive.py (stdlib unittest, excluded from the shipped
  package via %%install - see gotcha about requirements.txt feeding the
  runtime venv).
- %%install now excludes test_*.py from the shipped backend/ - the *.py
  glob there previously would have shipped the new test file into
  production installs too, harmlessly but needlessly.

* Mon Jul 06 2026 Developer <dev@example.com> - 0.1.0-16
- Adds a restore/download feature: pick any container's snapshot (not just
  the latest) and stream it as a zstd/gzip/uncompressed tar straight from
  its read-only .zfs/snapshot/<name>/ mount - never buffered whole on disk
  or in memory first. New Requires: zstd. Download endpoint accepts its JWT
  as a query param (auth_routes.get_current_admin_from_query_token) rather
  than the Authorization header, since a native browser download (the only
  way to stream a large file to disk without holding it all in page memory)
  can't attach custom headers. Actually restoring the result onto a source
  host stays a deliberate manual step - this app still never writes to a
  source host itself.
- Adds live "Running" status (vault_systemd.is_pull_running, straight from
  systemctl is-active) so the dashboard/host page can show a pull is
  actively in progress instead of just the last recorded (possibly stale
  "failed") result - previously there was no way to tell a fresh pull was
  already fixing a prior failure.
- Adds a manual "run now" trigger per host (systemctl start --no-block) and
  a vault-wide storage card on the dashboard (ZFS pool used/available, not
  just one host's slice of it).
- Pull-log viewer now scopes to the journal's own _SYSTEMD_INVOCATION_ID for
  the unit's most recent run instead of a +/-15 minute time window - the
  time window risked blending in an adjacent run's lines whenever pulls
  happened close together, making it impossible to tell if the shown log
  was actually current. Also adds real per-line timestamps (-o short-iso).

* Fri Jul 03 2026 Developer <dev@example.com> - 0.1.0-3
- %post now spells out that Caddy is NOT installed/configured by this
  package (stock Caddyfile only serves its own welcome page - does not
  reverse-proxy to the app), gives the exact "dnf install caddy + cp
  Caddyfile + firewall-cmd --add-service=http" steps, and explicitly notes
  SELinux (enforcing, unconfined_service_t) is rarely the actual cause of
  "can't reach the UI" - hit live 2026-07-03 on 192.0.2.11, where both
  the stock Caddyfile and a closed firewalld port were the real blockers

* Thu Jul 02 2026 Developer <dev@example.com> - 0.1.0-2
- Print next-steps instructions (env setup, Caddy, first-account URL) in
  %post instead of only in %description - Joe-the-sysadmin feedback after
  first real install on 192.0.2.11
- Dashboard now shows persistent ZFS kernel module status plus a loud
  alert banner if the module isn't loaded for the running kernel (e.g.
  after an unattended kernel update dkms failed to rebuild against) -
  new vault_zfs.module_status(), folded into GET /api/alerts/summary

* Wed Jul 01 2026 Developer <dev@example.com> - 0.1.0-1
- Initial packaging: FastAPI backend + prebuilt React frontend, systemd
  service on 127.0.0.1, Caddy reverse-proxy sketch, SQLite+WAL auth DB
- venv vendored at build time via pip install - needs network access
  during rpmbuild; if the builder is sandboxed, pre-fetch wheels instead
  (same category of risk as nspawn-vault.spec's ZFS repo caveat)
