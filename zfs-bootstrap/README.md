# nspawn-vault-zfs-bootstrap

One-script bootstrap that adds the OpenZFS EL repo and installs `zfs` via
DKMS, before `nspawn-vault` itself can be installed.

## Why this is its own package

`nspawn-vault` (`../engine/`) declares `Requires: zfs`. ZFS is not in
AlmaLinux/EL's own repos (CDDL/GPL license conflict), so dnf can't resolve
that dependency until the OpenZFS repo exists and `zfs`/`zfs-dkms` is
installed. That means the setup script can never live inside the
`nspawn-vault` RPM: dnf would refuse to even install a package containing
it, since its own `Requires: zfs` would already be unresolvable.

This package carries **no dependencies at all**, so dnf can always install
it first - regardless of what repos are configured on a fresh box - and
only then does `nspawn-vault-setup-zfs` become reachable on disk to run.

## Install order for a fresh vault host

```bash
dnf install nspawn-vault-zfs-bootstrap-<version>.<dist>.noarch.rpm
nspawn-vault-setup-zfs
dnf install nspawn-vault-<version>.<dist>.noarch.rpm
```

`nspawn-vault-setup-zfs` is idempotent - it exits immediately if `zfs` is
already on `$PATH`, so re-running it (or having it pre-installed on a
golden image) is harmless.

## Building the RPM

```bash
tar czf nspawn-vault-zfs-bootstrap.tar.gz \
    --transform 's,^zfs-bootstrap,nspawn-vault-zfs-bootstrap,' \
    -C /root/nspawn-vault zfs-bootstrap
rpmbuild -bb nspawn-vault-zfs-bootstrap.spec
```

Built via the internal build tool in practice - see `../CLAUDE.md`.
