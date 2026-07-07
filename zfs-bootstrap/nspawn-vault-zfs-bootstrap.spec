Name:           nspawn-vault-zfs-bootstrap
Version:        0.1.0
Release:        1%{?dist}
Summary:        One-time OpenZFS/DKMS bootstrap for nspawn-vault
License:        LGPL-2.1-or-later
URL:            https://github.com/realmcuser/nspawn-vault
Source0:        nspawn-vault-zfs-bootstrap.tar.gz
BuildArch:      noarch

%description
nspawn-vault (the pull-based backup engine) declares "Requires: zfs", but
ZFS itself is not in AlmaLinux/EL's own repos (CDDL/GPL license conflict) -
dnf can't resolve that dependency until the OpenZFS repo has been added and
zfs-dkms installed. That chicken-and-egg problem means the setup step
cannot be shipped inside the nspawn-vault RPM itself: dnf would refuse to
even install a package containing it, since the Requires would already be
unresolvable at that point.

This package has NO dependency on zfs - it only installs one script,
nspawn-vault-setup-zfs, that a real end user (with no pre-existing ZFS
repo configured) can install and run BEFORE nspawn-vault itself:

  1. dnf install nspawn-vault-zfs-bootstrap-<version>.<dist>.noarch.rpm
  2. nspawn-vault-setup-zfs
  3. dnf install nspawn-vault-<version>.<dist>.noarch.rpm

%prep
%setup -q -n nspawn-vault-zfs-bootstrap

%build
# nothing to build, one shell script

%install
install -d %{buildroot}%{_sbindir}
install -m 0755 usr/sbin/nspawn-vault-setup-zfs %{buildroot}%{_sbindir}/nspawn-vault-setup-zfs

%files
%{_sbindir}/nspawn-vault-setup-zfs

%post
echo "Run 'nspawn-vault-setup-zfs' now, then install nspawn-vault itself."

%changelog
* Thu Jul 02 2026 Developer <dev@example.com> - 0.1.0-1
- Split out of engine/scripts/setup-zfs.sh into its own package: that
  script can never be shipped inside nspawn-vault.spec itself (it Requires
  zfs, so dnf can't resolve the dependency before this script has run) -
  see nspawn-vault.spec's %description for the full chicken-and-egg
  reasoning. This package deliberately carries no Requires at all so dnf
  can always install it first, regardless of what repos are configured yet.
