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
