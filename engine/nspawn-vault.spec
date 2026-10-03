Name:           nspawn-vault
Version:        0.1.0
Release:        1%{?dist}
Summary:        Pull-based backup vault for nspawn-cockpit hosts
License:        LGPL-2.1-or-later
URL:            https://github.com/realmcuser/nspawn-vault
Source0:        nspawn-vault.tar.gz
BuildArch:      noarch

%{?systemd_requires}
BuildRequires:  systemd-rpm-macros

Requires:       zfs
Requires:       rsync
Requires:       openssh-clients
Requires:       python3
Requires:       curl
Requires:       systemd

%description
Backup vault for cockpit-nspawn hosts using a pull model: the vault
initiates every backup over SSH with a forced, read-only command on the
source host, so a compromised nspawn host never holds credentials that
could reach or modify the vault. Backups land on ZFS, snapshotted after
every pull, with GFS retention and a dead-man's switch that alerts via
Pushover/Slack if a source host stops reporting successful pulls.

ZFS itself is NOT provided by this package (license reasons keep it out
of the AlmaLinux/EL repos), and this package Requires it - so the ZFS
setup step CANNOT be shipped inside this RPM (dnf would refuse to install
it before zfs exists). Install nspawn-vault-zfs-bootstrap first and run
`nspawn-vault-setup-zfs` (see ../zfs-bootstrap/) BEFORE installing this
RPM, to add the OpenZFS repo and install zfs via DKMS.

After install:
  1. %{_libexecdir}/nspawn-vault/init-pool.sh /dev/vdb   # once, creates the zpool
  2. ssh-keygen -t ed25519 -f /root/.ssh/nspawn-vault -N ""
  3. cp %{_sysconfdir}/nspawn-vault/notify.conf.example %{_sysconfdir}/nspawn-vault/notify.conf
     (fill in Pushover/Slack creds, chmod 600)
  4. mkdir -p %{_sysconfdir}/nspawn-vault/<host>/ and list container names
     one per line in %{_sysconfdir}/nspawn-vault/<host>/containers
  5. systemctl enable --now nspawn-vault-pull@<host>.timer
     (repeat per source host)

%prep
%setup -q -n nspawn-vault

%build
# nothing to build, plain scripts + systemd units

%install
install -d %{buildroot}%{_libexecdir}/nspawn-vault
install -m 0755 usr/libexec/nspawn-vault/*.sh %{buildroot}%{_libexecdir}/nspawn-vault/
install -m 0755 usr/libexec/nspawn-vault/gfs.py %{buildroot}%{_libexecdir}/nspawn-vault/

install -d %{buildroot}%{_sysconfdir}/nspawn-vault
install -m 0644 etc/nspawn-vault/*.example %{buildroot}%{_sysconfdir}/nspawn-vault/

install -d %{buildroot}%{_unitdir}
install -m 0644 systemd/*.service systemd/*.timer %{buildroot}%{_unitdir}/

install -d %{buildroot}%{_sharedstatedir}/nspawn-vault/state

%files
%dir %{_libexecdir}/nspawn-vault
%{_libexecdir}/nspawn-vault/*
%dir %{_sysconfdir}/nspawn-vault
%{_sysconfdir}/nspawn-vault/*.example
%{_unitdir}/nspawn-vault-check.service
%{_unitdir}/nspawn-vault-check.timer
%{_unitdir}/nspawn-vault-prune.service
%{_unitdir}/nspawn-vault-prune.timer
%{_unitdir}/nspawn-vault-pull@.service
%{_unitdir}/nspawn-vault-pull@.timer
%dir %{_sharedstatedir}/nspawn-vault
%dir %{_sharedstatedir}/nspawn-vault/state

%post
%systemd_post nspawn-vault-check.timer nspawn-vault-prune.timer
# %%systemd_post only runs `systemctl preset`, which defers to whatever
# preset policy the distro ships - on AlmaLinux/EL10 that's
# /usr/lib/systemd/system-preset/99-default-disable.preset ("disable *"),
# a catch-all that wins for any unit without its own explicit preset rule.
# Confirmed live 2026-07-07: both timers stayed disabled after a real
# install on 192.0.2.11, silently - GFS retention therefore never
# actually ran and snapshots piled up unbounded (49+ for one container
# alone). Unlike the per-host pull timers (enabled explicitly via the web
# UI once a host is configured), these two are singleton, always-safe,
# no-configuration-prerequisite timers - so enable them here directly
# instead of trusting presets alone.
systemctl enable --now nspawn-vault-check.timer nspawn-vault-prune.timer >/dev/null 2>&1 || :
echo ""
echo "nspawn-vault installed. Next steps:"
echo "  1. %{_libexecdir}/nspawn-vault/init-pool.sh /dev/vdb   # once, creates the zpool"
echo "  2. ssh-keygen -t ed25519 -f /root/.ssh/nspawn-vault -N \"\""
echo "  3. cp %{_sysconfdir}/nspawn-vault/notify.conf.example %{_sysconfdir}/nspawn-vault/notify.conf"
echo "     (fill in Pushover/Slack creds, chmod 600)"
echo "  4. mkdir -p %{_sysconfdir}/nspawn-vault/<host>/ and list container names"
echo "     one per line in %{_sysconfdir}/nspawn-vault/<host>/containers"
echo "  5. systemctl enable --now nspawn-vault-pull@<host>.timer   (repeat per source host)"
echo "  6. If nspawn-vault-web is also installed, its dashboard shows pull status"
echo "     and lets admins edit steps 3-5 without touching config files by hand."
echo ""

%preun
%systemd_preun nspawn-vault-check.timer nspawn-vault-prune.timer

%postun
%systemd_postun_with_restart nspawn-vault-check.timer nspawn-vault-prune.timer

%changelog
* Sat Oct 03 2026 Developer <dev@example.com> - 0.1.0-17
- pull.sh no longer treats rsync exit code 24 ("partial transfer due to
  vanished source files") as a hard pull failure. Found live on
  besten.alsike.minten.se's hermes-agent container: an actively-running
  process there constantly rewrites/deletes its own scratch files and a
  SQLite WAL/SHM pair, which occasionally vanish mid-rsync - a benign race
  on any busy container, not a real backup problem, but it was logging a
  "failed" pull and tripping check-stale.sh's dead-man's-switch alert
  immediately (14 false ALERTs in 30 days for this one container, 0 for
  its neighbor fhdslackbot on the same host). Exit 24 now just logs a
  warning and the pull proceeds to the zfs snapshot as normal; every other
  nonzero rsync exit code still fails the pull exactly as before.

* Mon Aug 03 2026 Developer <dev@example.com> - 0.1.0-16
- Adds an optional, generic pre-snapshot-command hook per container:
  pull.sh now calls the source host's `pre-snapshot <name>` (new
  dispatch.sh-whitelisted command, matching source-host/pre-snapshot.sh)
  right before the existing `snapshot-db` MariaDB dump. Runs a single
  operator-authored command inside the container via `systemd-run
  --machine=`, from /etc/cockpit-nspawn/pull/<name>.hook - no-ops if that
  file doesn't exist. Built for FreeIPA/postgres containers (`ipa-backup`,
  `pg_dump`, etc.) that snapshot-db.sh's MariaDB-specific dump doesn't
  cover, without teaching pull.sh anything about databases: the hook
  command is entirely local config on the source host, same trust
  boundary as snapshot-db.sh's own DB-credentials file - the vault never
  sees or sends it, dispatch.sh still only ever runs one fixed script
  name. The existing snapshot-db/MariaDB path is unchanged and stays
  first-class, not replaced.
- Both the hook and snapshot-db now block the pull on failure with the
  *real* captured failure reason (new run_remote() helper in pull.sh,
  replacing a bare `|| fail "snapshot-db failed"`) instead of a generic
  canned string - e.g. "snapshot-db: mysqldump failed - is MariaDB
  running and the credentials in ... correct?" shows up directly as
  last_pull_msg. Blocking-on-failure and top-severity alerting both
  already existed (a "failed" pull already outranks "stale" and already
  triggers the loudest banner in nspawn-vault-web) - this was purely a
  message-quality gap, not a missing alert path.
- Fixes a latent JSON-injection bug in fail(): it interpolated its
  message argument into a printf format string with no escaping at all.
  Never hit before (every prior caller passed a static string), but
  run_remote() now passes real captured command output, which can
  contain '"' or a newline and would otherwise write invalid JSON to the
  container's state file.
- Backports write_status()/explicit error handling into
  source-host/snapshot-db.sh (the canonical copy - see
  source-host/README.md) from the nspawn-cockpit repo's own copy, which
  had already drifted ahead with this improvement. See
  nspawn-vault-web 0.1.0-34 for the matching per-container pull-log fix
  that surfaces these new failure messages without the neighboring
  container's log lines mixed in.

* Thu Jul 30 2026 Developer <dev@example.com> - 0.1.0-15
- Suppresses ransomware-suspected false positives caused by a real
  dnf/dnf-automatic package update, instead of just tolerating a bigger
  threshold. Found live on fhdcore-jf2: a routine dnf-automatic update
  rebuilds its whole runtime environment and touches ~12,000 files in
  one pull - no fixed threshold both catches real ransomware and
  tolerates an update that size.
- pull.sh already rsyncs the whole container filesystem before computing
  the diff, so /var/log/dnf.log (and dnf.rpm.log) - an ordinary file in
  that tree, with its original mtime preserved by rsync -a - can be
  compared against the previous snapshot's own creation time (zfs
  already tracks this as raw epoch seconds via `get -p creation`). If
  the log is newer than the previous snapshot, a real package
  transaction happened in between and the diff is explained without
  being ransomware - ransomware_suspected/the zfs hold/the pause all get
  skipped, but changed_entries still records the real count and a new
  dnf_update_detected:true field in the state JSON keeps the reason
  visible rather than the alert just silently not firing. Catches a
  human running "dnf upgrade" manually too, not just dnf-automatic
  specifically - deliberately not gated on whether dnf-automatic.timer
  is enabled, since the log-mtime check is strictly more general. See
  nspawn-vault-web 0.1.0-33 for the matching dashboard hint.

* Tue Jul 28 2026 Developer <dev@example.com> - 0.1.0-14
- Fixes read_host_language() (added in 0.1.0-13): it redirected `tr`'s
  stdin from `<host>/notify-language` without checking the file exists
  first, so every host WITHOUT that new opt-in file (i.e. almost every
  host, since it's brand new) logged a "No such file or directory" error
  on every single check-stale.sh run instead of silently defaulting to
  "sv" the way it was supposed to. Caught live on the production vault
  immediately after deploying 0.1.0-13 - real end-to-end email-dispatch
  testing on a live host with the new feature configured, not just a
  syntax check, is what surfaced it (fhdcore-jf.vpn.fhd.se, which has no
  notify-language file, tripped it on the very first run). Guarded with
  the same `[ -f "$f" ] || ...` pattern read_admin_contact() already used
  correctly.

* Tue Jul 28 2026 Developer <dev@example.com> - 0.1.0-13
- check-stale.sh's per-host email alert now splits <host>/notify-email
  recipients into two categories: "admin" (bare address, or explicit
  ":admin" suffix - unchanged, gets exactly today's technical alert) and
  "user" (":user" suffix - gets a friendlier message naming the affected
  container(s) instead of raw result/age/threshold values). Fully
  backward compatible: every existing notify-email file (no suffixes at
  all) keeps sending the identical admin email it always did.
- Two new optional per-host files back the "user" email: admin-contact
  (2 lines: contact name, then contact info) shown as a "contact X for
  help" line, and notify-language (one line, "sv" or "en", default "sv")
  selecting which fully-localized template it's sent in. Both are plain
  reads, never `source`d as shell.
- send-email.sh itself is unchanged - category/language selection and
  the two distinct message bodies are entirely a check-stale.sh concern,
  dispatched as two separate send-email.sh calls per host when both
  categories have problems to report. See nspawn-vault-web 0.1.0-30 for
  the matching Admin UI (per-recipient category, admin-contact fields,
  language selector).

* Tue Jul 28 2026 Developer <dev@example.com> - 0.1.0-12
- Auto-pauses pull.sh for a container once ransomware_suspected trips,
  instead of just alerting: it now places a `zfs hold` (tag
  nspawn-vault-ransomware) on the last known-good snapshot - protecting it
  from gfs-prune.sh regardless of GFS retention settings or storage
  pressure - and writes a marker under the new
  /var/lib/nspawn-vault/state/paused/ that every future pull for that
  container checks first, skipping entirely (without touching the state
  JSON) until an admin acknowledges the event in nspawn-vault-web. Built
  after a live incident where a colleague's brand-new container tripped
  the heuristic during normal setup (bulk file copy-in) - alerting alone
  doesn't stop new (possibly-bad) snapshots from continuing to displace
  the last good one out of GFS's retention window if nobody notices in
  time; pausing does. See nspawn-vault-web 0.1.0-29 for the matching
  "Acknowledge & resume" UI/endpoint that releases the hold and clears
  the pause marker.
- Adds RANSOMWARE_GRACE_PULLS (new notify.conf key, default 3): the
  zfs-diff check is skipped for a container's first N pulls, not just the
  very first one as before - covers the common case of a newly created
  container being actively populated over several pulls, not just one.
  Deliberate default *behavior change* (the check used to start on pull
  #2, now starts on pull #4 by default) aimed at exactly this scenario.
- gfs-prune.sh no longer aborts an entire prune run if a single
  `zfs destroy` fails (e.g. against a held snapshot) - warns and continues
  with the rest of that dataset's prunable snapshots instead. Needed for
  the hold above to actually protect anything; also a general robustness
  fix independent of it.

* Thu Jul 16 2026 Developer <dev@example.com> - 0.1.0-11
- Adds a repeat-alert backoff to check-stale.sh: previously, an ongoing
  incident (a source host down for hours) re-fired Pushover/Slack/email on
  every single 30-minute run for as long as it stayed unresolved. A small
  marker file per (host, container, problem-kind) under the new
  /var/lib/nspawn-vault/state/alerted/ now records the epoch of the last
  alert sent; a repeat alert for the same still-unresolved problem is
  suppressed until ALERT_BACKOFF_HOURS (new notify.conf key, default 6,
  0 disables backoff entirely) has passed. Applies independently to both
  the staleness/failed alert and the new ransomware alert (0.1.0-10) per
  container. The marker is cleared as soon as the container is next seen
  OK/clean, so a genuinely new incident always alerts immediately - only
  a *persisting* one gets throttled. Exit code and journal output
  (`SUPPRESSED (backoff): ...`) are unaffected either way, so
  `systemctl status`/journal always reflect the real current state even
  while outbound notifications are being throttled.
- Found live 2026-07-16: an overnight outage on one source host produced
  a real burst of automated stale-alert emails, on an account that also
  had a vacation autoresponder running - plausibly enough combined
  outbound traffic to trip the SMTP relay's own anti-abuse auth lock
  (unconfirmed, separate/pre-existing issue, not itself fixed by this
  change - see nspawn-vault-web 0.1.0-28 for the matching Admin field).

* Thu Jul 16 2026 Developer <dev@example.com> - 0.1.0-10
- Adds a ransomware-detection heuristic to pull.sh: after every successful
  snapshot, diffs it against the previous one via `zfs diff` (cheap - reads
  ZFS's own change metadata, not a filesystem walk) and writes the changed-
  entry count plus a "ransomware_suspected" boolean into the container's
  state JSON when it reaches RANSOMWARE_DIFF_THRESHOLD (new key in
  notify.conf, default 500, 0 disables the check). First-ever pull for a
  container is skipped (nothing to diff against yet).
- check-stale.sh now also reads these two fields per container on its
  existing 30-minute pass and fires an immediate alert (same Pushover/
  Slack/email channels as the stale/failed checks) whenever
  ransomware_suspected is set - independent of and in addition to the
  existing success/staleness check, since a pull can succeed normally while
  its content is suspect. See nspawn-vault-web 0.1.0-27 for the matching
  dashboard banner/status - a suspected event is visible in the UI as soon
  as the pull finishes, but Pushover/Slack/email only re-fire on
  check-stale.sh's next 30-minute pass, by design (keeps the alert
  dispatch logic in one place rather than duplicated into pull.sh). One
  global threshold rather than a per-container one, deliberately, for
  simplicity - container sizes/churn vary, so a fleet-wide default may need
  tuning via the new Admin field if false positives show up in practice.

* Fri Jul 10 2026 Developer <dev@example.com> - 0.1.0-9
- Adds email as a third dead-man's-switch alert channel alongside
  Pushover/Slack, via a shared SMTP relay config (new SMTP_* keys in
  notify.conf) and new per-source-host recipients in
  %{_sysconfdir}/nspawn-vault/<host>/notify-email (one address per line) -
  built for a ~200-host fleet where different people need to be told about
  different source hosts, not one global recipient list. New
  usr/libexec/nspawn-vault/send-email.sh sends via curl (STARTTLS/587 or
  implicit TLS/465, both configurable), shared by check-stale.sh and
  nspawn-vault-web's new "send test email" admin button.
- check-stale.sh now loops configured hosts/containers
  (%{_sysconfdir}/nspawn-vault/<host>/containers) instead of globbing
  %{_sharedstatedir}/nspawn-vault/state/*.json directly - needed to know
  which host a container belongs to (the state filename alone can't be
  split back into an unambiguous host/container pair), and brings it in
  line with how nspawn-vault-web already enumerates hosts. Existing
  per-container Pushover/Slack alerts are unchanged; email is batched one
  message per host (listing every problem container) rather than one email
  per container, to avoid flooding two recipients across a large fleet.
- SMTP_USER/SMTP_PASS go through the same shell-quoting as the existing
  Pushover/Slack secrets (gotcha #1 - check-stale.sh sources notify.conf as
  root) - re-verified live with a $()/backtick injection payload, stays inert.

* Tue Jul 07 2026 Developer <dev@example.com> - 0.1.0-8
- %%post now explicitly `systemctl enable --now`s nspawn-vault-check.timer
  and nspawn-vault-prune.timer instead of relying solely on %%systemd_post's
  `systemctl preset` call - AlmaLinux/EL10's catch-all
  99-default-disable.preset ("disable *") silently left both timers
  disabled after a real install, so GFS retention never actually ran and
  snapshots accumulated without limit. Found live on 192.0.2.11
  (49+ snapshots for a single container, prune timer confirmed disabled
  and its service journal empty - it had never fired once).

* Thu Jul 02 2026 Developer <dev@example.com> - 0.1.0-2
- Print next-steps instructions in %post instead of only in %description
  (rpm -qi is not something a fresh install surfaces automatically) -
  Joe-the-sysadmin feedback after first real install on 192.0.2.11
- setup-zfs.sh moved to its own package, nspawn-vault-zfs-bootstrap
  (../zfs-bootstrap/) - reference updated accordingly

* Wed Jul 01 2026 Developer <dev@example.com> - 0.1.0-1
- Initial packaging: pull.sh, pull-host.sh, check-stale.sh, gfs-prune.sh,
  gfs.py, prune-all.sh moved from /usr/local/lib to /usr/libexec (FHS)
- Fixed pull.sh dataset default: was hardcoded to "source0", now derives
  from $HOST so multiple source hosts don't collide under the same
  dataset path
- Added templated nspawn-vault-pull@.timer/.service so new source hosts
  can be added with "systemctl enable --now nspawn-vault-pull@<host>.timer"
  instead of hand-writing a new unit file per host
- Added init-pool.sh as an explicit manual first-run step (not in %post,
  creating a zpool is destructive)
- setup-zfs.sh deliberately NOT packaged here (chicken-and-egg: this RPM
  Requires zfs, so the script that installs zfs can't live inside it) -
  ships as a standalone file in scripts/, distributed separately
