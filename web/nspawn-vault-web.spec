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
* Thu Oct 08 2026 Developer <dev@example.com> - 0.1.0-35
- GET /api/admin/vault-key (vault_ssh.get_public_key()) now returns both
  the bare public key (for pasting into cockpit-nspawn's own "Enable pull
  backup" toggle, which builds the forced-command authorized_keys line
  itself) and the full, pre-built authorized_keys_line (restrict,command=
  "/usr/local/lib/nspawn-pull/dispatch.sh" + the key, for a manual install
  that doesn't use the toggle). Previously only handed out the bare key
  for both paths - found live that this invites pasting the bare key
  straight into a source host's authorized_keys with no restrict= wrapper
  at all, giving the vault's key a fully unrestricted root login on that
  host instead of the intended dispatch.sh-only access. Confirmed live on
  a manually-onboarded source host (2026-10-08).
- Admin.jsx's Source Hosts panel now shows both forms with separate
  labels/copy buttons and a warning against splitting the full line
  apart, instead of one ambiguous key field.

* Mon Aug 03 2026 Developer <dev@example.com> - 0.1.0-34
- fetch_pull_log() (vault_systemd.py) now accepts a container name and
  slices the returned journal text down to just that container's own
  block, using the "--- <name> ---" markers pull-host.sh already prints
  before each container in a host's pull run - falls back to the full
  per-host text if the marker isn't found (older/rotated-out journal).
  get_container_pull_log() (vault_routes.py) passes the container through
  instead of dropping it. Fixes a real, previously-documented-but-not-
  fixed limitation: the "view log" modal for one container was showing
  the ENTIRE host's pull run, including every other container pulled in
  the same invocation - confirmed confusing live (fhdcore-jf2's log also
  showed unrelated samba-fhdcore-jf2 lines). HostDetail.jsx's log modal
  gets a small caption noting the log is filtered, for the rare fallback
  case. See nspawn-vault 0.1.0-16 for the new pre-snapshot hook whose
  failure messages this makes actually readable without the noise.

* Thu Jul 30 2026 Developer <dev@example.com> - 0.1.0-33
- Host detail page shows a new calm, non-alarming hint on a container
  whose last pull had a large diff that was explained away by a real
  dnf/dnf-automatic update (nspawn-vault 0.1.0-15's dnf_update_detected)
  instead of just going quiet with no visible trace of the (large but
  legitimate) changed-file count. get_host_detail()'s per-container
  payload gains the passthrough field.

* Tue Jul 28 2026 Developer <dev@example.com> - 0.1.0-32
- Merges the Source Hosts table's separate "Containers" and "Email
  alerts" columns into one "Containers & email alerts" column, with
  Containers stacked above Emails (divider between them) instead of
  side by side. Found live: with 5 columns sharing the table's width,
  the notify-settings panel's own edit-mode width squeezed the
  neighboring Containers column uncomfortably narrow whenever the panel
  was open. Stacking them in one column instead of splitting the row
  horizontally gives whichever section is being edited the full column
  width to itself.

* Tue Jul 28 2026 Developer <dev@example.com> - 0.1.0-31
- Fixes two real usability issues in 0.1.0-30's notify-settings panel,
  found live by Johan testing it minutes after it shipped: the two new
  contact inputs (admin-contact name/email-phone) were placed side by
  side in a 2-column grid inside an already-narrow table cell, so their
  placeholder text ("Admin-kontaktens namn" / "...e-post/telefon")
  visually clipped down to just "Admin-kontaktens" in both boxes -
  impossible to tell them apart. Stacked them vertically instead and
  gave each an explicit label above the input (matching the language
  selector's existing label pattern) instead of relying on placeholder
  text alone.
- The Swedish locale mixed the bare English word "User" into otherwise-
  Swedish sentences ("Språk för User-mejl") despite the category hint
  right above it already saying "kundmottagare" - standardized on "kund"
  throughout the Swedish strings for this feature, matching the hint's
  own wording. English strings unchanged ("User" is native there).

* Tue Jul 28 2026 Developer <dev@example.com> - 0.1.0-30
- Admin > Source Hosts' email-recipients editor gains: a category
  (admin/user) per address, entered inline via a ":user" suffix on the
  line (documented in a new hint under the textarea); an "admin contact"
  name + email/phone shown to "user"-category recipients in their
  friendlier alert email; and a Swedish/English selector for that same
  email. Backed by a new combined
  PUT /api/admin/hosts/{host}/notify-settings (replaces the old
  PUT .../emails, which only ever carried a bare address list) and
  matching read/write functions in vault_config.py
  (read_host_emails/write_host_emails now carry category,
  read_host_admin_contact/write_host_admin_contact,
  read_host_language/write_host_language). See nspawn-vault 0.1.0-13 for
  the check-stale.sh side that actually sends the two different emails.
- Non-edit-mode email pills are now visually distinguished by category
  (admin vs. user) so the split is scannable without entering edit mode.

* Tue Jul 28 2026 Developer <dev@example.com> - 0.1.0-29
- Adds "Acknowledge & resume" to the host detail page: when a container is
  auto-paused by pull.sh (nspawn-vault 0.1.0-12, on ransomware_suspected),
  its status row now shows this instead of just the existing ransomware
  hint, admin-only. Confirming calls the new POST
  /api/admin/hosts/{host}/containers/{container}/acknowledge-ransomware,
  which releases the zfs hold pull.sh placed on the last known-good
  snapshot and deletes the pause marker so the container's normal pull
  timer resumes, and logs the action (with an optional free-text note) to
  the existing audit log. Deliberately does not clear
  ransomware_suspected itself in the state JSON - the row instead shows
  an "awaiting recheck" hint until the next real pull confirms the
  container is actually clean again.
- get_host_detail()'s per-container payload gains a "paused" boolean;
  Admin > Notifications gains a "Ransomware check grace period" field for
  the matching new RANSOMWARE_GRACE_PULLS notify.conf key (nspawn-vault
  0.1.0-12), same plaintext/digits-only/shell-quoted pattern as the
  existing ransomware threshold and backoff-hours fields.

* Thu Jul 16 2026 Developer <dev@example.com> - 0.1.0-28
- Exposes the new ALERT_BACKOFF_HOURS setting (nspawn-vault 0.1.0-11) in
  Admin > Notifications - a new "Repeat-alert backoff" field, plaintext
  like the ransomware threshold (not a secret), validated digits-only
  server-side and written through the same shell-quoting-safe path as
  every other notify.conf field. GET/PUT /api/admin/settings/notify carry
  it through unchanged otherwise.

* Thu Jul 16 2026 Developer <dev@example.com> - 0.1.0-27
- Threads through a new "ransomware_suspected" status the engine can now set
  on a container (see nspawn-vault 0.1.0-10's zfs-diff heuristic in pull.sh):
  vault_state.compute_status() returns a new "ransomware" status ranked
  above "failed" (both in vault_state and vault_routes._STATUS_RANK), and
  GET /api/hosts/{host} and /api/alerts/summary (new "ransomware_hosts" key,
  folded into has_alert) surface it.
- New Dashboard banner (RansomwareAlertBanner.jsx, same deliberately-loud
  styling as StaleAlertBanner/ZfsAlertBanner) rides the existing 30s
  /api/alerts/summary poll already in Dashboard.jsx - no new polling
  infrastructure needed for it to update on its own for anyone already
  sitting on the dashboard. HostDetail shows the changed-file count inline
  under the container's status badge; StatusBadge gets a distinct
  "ransomware" variant.
- New Admin > Notifications field, RANSOMWARE_DIFF_THRESHOLD (also readable/
  writable via GET/PUT /api/admin/settings/notify, plaintext like
  smtp_port - not a secret), validated server-side as digits-only before
  being written through the existing _shell_quote() path (gotcha #1 -
  check-stale.sh sources notify.conf as root). Re-verified live with a
  $()-style injection payload in this new field - rejected with a 400, not
  written to disk.

* Sat Jul 11 2026 Developer <dev@example.com> - 0.1.0-26
- write_containers/write_host_emails/write_gfs_conf/write_notify_conf now
  write via a temp file + os.replace() instead of Path.write_text()
  straight onto the target - write_text() truncates the existing file in
  place before writing, leaving a real (if narrow) window where a
  concurrent reader (check-stale.sh's 30-minute timer sourcing notify.conf,
  or a "send test email" click landing mid-save) could read a
  partially-written file. Found live 2026-07-11 while setting up the new
  SMTP relay settings - several "send test email" attempts got an
  intermittent curl "login denied", some immediately following an Admin
  save. os.replace() is an atomic rename on the same filesystem, so a
  reader now always sees either the complete old file or the complete new
  one, never a partial one. notify.conf's 0600 permissions are applied to
  the temp file before the rename, so the target never briefly exists with
  looser permissions either. Verified with a 200-write/4000-read
  concurrent stress test (zero partial reads) and re-ran the existing
  shell-quoting injection test against the refactored write path (still
  inert).

* Fri Jul 10 2026 Developer <dev@example.com> - 0.1.0-25
- Adds email alert configuration to Admin: SMTP relay settings (host, port,
  STARTTLS/implicit TLS, from address, optional auth) in the Notifications
  section, plus a "send test email" button that fires a real email
  synchronously via nspawn-vault (engine)'s new send-email.sh and shows the
  result inline - same pattern as the existing LDAP/SSH test-connection
  buttons. Also adds a per-source-host email recipients editor next to the
  existing container-list editor in the Source Hosts table
  (vault_config.read_host_emails/write_host_emails, new
  PUT /api/admin/hosts/{host}/emails) - up to a handful of addresses per
  host, validated server-side before ever touching disk (same
  validate-before-write pattern as hostnames/container names).
- New POST /api/admin/settings/notify/test-email and vault_email.py
  (subprocess wrapper around send-email.sh, mirrors vault_ssh.test_connection's
  {success, message} result shape). SMTP_USER/SMTP_PASS follow the existing
  "********" sentinel convention for secrets (never echoed back in plaintext).

* Wed Jul 08 2026 Developer <dev@example.com> - 0.1.0-24
- Adds a manual "Run prune now" button to the Admin GFS Retention section
  (vault_systemd.trigger_prune_now(), POST /api/admin/prune/trigger-now)
  - starts nspawn-vault-prune.service immediately instead of waiting for
  its daily 04:00 timer. Same --no-block pattern as the existing per-host
  "Run now" pull trigger. Handy right after changing GFS settings, or to
  reclaim space proactively. Verified live: triggered a real prune run,
  hermes-agent's snapshot count dropped from 41 to 27 immediately.

* Wed Jul 08 2026 Developer <dev@example.com> - 0.1.0-23
- Adds a per-container snapshot-retention figure to the HostDetail table:
  current snapshot count plus how many would remain after the next GFS
  prune run (vault_zfs.snapshot_retention()). Shells out to the exact same
  gfs.py the real nspawn-vault-prune.timer calls rather than
  reimplementing its bucket logic - the naive GH+GD+GW+GM+GY sum is NOT
  the right number here, since a recent snapshot can satisfy several
  buckets (hour/day/week/...) at once. Prompted by a real question about
  whether 40 stored snapshots for one container was too many - it wasn't
  (12 of them were already due for the next nightly prune), but there was
  no way to see that in the UI without doing the same manual check by hand.

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
