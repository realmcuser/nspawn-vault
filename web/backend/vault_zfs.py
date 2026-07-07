import os
import re
import subprocess

_DATASET_NAME_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_DB_DUMP_RELPATH = "var/tmp/cockpit-nspawn-db.sql"


def _run(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _safe(name: str) -> str:
    if not _DATASET_NAME_RE.match(name):
        raise ValueError(f"unsafe zfs identifier: {name!r}")
    return name


def list_datasets(pool: str = "vault") -> list[dict]:
    """Two-levels-deep datasets under the pool: pool/host/container.
    Mirrors prune-all.sh's `grep -E "${POOL}/[^/]+/[^/]+$"` filter."""
    pool = _safe(pool)
    proc = _run(["zfs", "list", "-H", "-p", "-o", "name,used,mountpoint", "-t", "filesystem", "-r", pool])
    if proc.returncode != 0:
        return []
    leaf_re = re.compile(rf"^{re.escape(pool)}/[^/]+/[^/]+$")
    result = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, used, mountpoint = parts
        if leaf_re.match(name):
            result.append({"name": name, "used_bytes": int(used), "mountpoint": mountpoint})
    return result


def pool_capacity(pool: str = "vault") -> dict | None:
    """Total used/available bytes for the whole pool (its root dataset) -
    not any one host's or container's slice of it. `available` already
    reflects genuinely free pool space (ZFS datasets share pool space
    unless a quota restricts them further, and none are set here), so this
    is the right level to query for a vault-wide storage figure rather than
    summing individual per-host/per-container datasets."""
    pool = _safe(pool)
    proc = _run(["zfs", "list", "-H", "-p", "-o", "used,avail", pool])
    if proc.returncode != 0:
        return None
    parts = proc.stdout.strip().split("\t")
    if len(parts) != 2:
        return None
    try:
        used, avail = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return {"used_bytes": used, "available_bytes": avail, "total_bytes": used + avail}


# Hardcoded, documented constant rather than an admin-configurable setting -
# same precedent as vault_state.THRESHOLD_MIN (the staleness threshold).
# Revisit if this ever actually needs to be tunable per deployment.
STORAGE_ALERT_FREE_PERCENT = 10


def storage_status(pool: str = "vault") -> dict | None:
    """pool_capacity() plus whether free space has dropped below the
    dead-man's-switch-style alert threshold - folded into
    GET /api/alerts/summary and GET /api/vault/storage so both the loud
    banner and the dashboard's storage card work off the same number."""
    capacity = pool_capacity(pool)
    if capacity is None:
        return None
    total = capacity["total_bytes"]
    percent_free = (capacity["available_bytes"] / total * 100) if total else 0.0
    return {
        **capacity,
        "percent_free": percent_free,
        "ok": percent_free >= STORAGE_ALERT_FREE_PERCENT,
    }


def dataset_used_bytes(dataset: str) -> int | None:
    dataset = _safe(dataset)
    proc = _run(["zfs", "get", "-H", "-p", "-o", "value", "used", dataset])
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def dataset_exists(dataset: str) -> bool:
    dataset = _safe(dataset)
    proc = _run(["zfs", "list", "-H", "-o", "name", dataset])
    return proc.returncode == 0


def dataset_mountpoint(dataset: str) -> str | None:
    dataset = _safe(dataset)
    proc = _run(["zfs", "get", "-H", "-o", "value", "mountpoint", dataset])
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value if value not in ("", "-", "none") else None


def has_db_dump(dataset: str) -> bool:
    """Whether the container's last successful pull included a DB dump -
    checked against the actual synced files on disk (source of truth),
    not the ephemeral per-run state JSON, so a later *failed* pull attempt
    doesn't make this flicker off: snapshot-db.sh (source-host/snapshot-db.sh
    in this repo) writes the dump to var/tmp/cockpit-nspawn-db.sql inside
    the container tree, which rsync then pulls down like everything else -
    it only ever disappears from here once a NEWER successful pull without
    a DB dump overwrites it (via rsync --delete)."""
    mountpoint = dataset_mountpoint(dataset)
    if not mountpoint:
        return False
    return os.path.isfile(os.path.join(mountpoint, _DB_DUMP_RELPATH))


def module_status() -> dict:
    """Whether the zfs kernel module is actually loaded for the CURRENTLY
    RUNNING kernel. Catches the case where dnf auto-updated the kernel and
    dkms's own postinstall trigger never (re)built the module for it - see
    CLAUDE.md gotcha #7, hit live 2026-07-02: dkms status can silently sit
    at "added" instead of "installed" after a kernel-devel install, and a
    plain `zfs`/`zpool` command then fails outright, breaking every pull on
    the vault with no warning short of checking this explicitly."""
    kernel = _run(["uname", "-r"]).stdout.strip()
    lsmod = _run(["lsmod"])
    loaded = any(
        line.split()[0] == "zfs" for line in lsmod.stdout.splitlines() if line.strip()
    )
    dkms = _run(["dkms", "status", "-k", kernel])
    built_for_running_kernel = ": installed" in dkms.stdout
    return {
        "running_kernel": kernel,
        "loaded": loaded,
        "built_for_running_kernel": built_for_running_kernel,
        "ok": loaded and built_for_running_kernel,
    }


def list_snapshots(dataset: str) -> list[dict]:
    """Sorted oldest-first (matches gfs-prune.sh's `-s creation`), so callers
    take [-1] for the latest snapshot."""
    dataset = _safe(dataset)
    proc = _run(["zfs", "list", "-H", "-p", "-o", "name,creation", "-t", "snapshot", "-s", "creation", "-r", dataset])
    if proc.returncode != 0:
        return []
    result = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        name, creation = parts
        try:
            result.append({"name": name, "creation_epoch": int(creation)})
        except ValueError:
            continue
    return result
