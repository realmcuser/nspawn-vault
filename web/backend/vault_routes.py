import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
from urllib.parse import unquote

import vault_config
import vault_state
import vault_zfs
import vault_systemd
import vault_ssh
import vault_email
import vault_archive
import vault_audit
from auth_routes import get_current_user, get_current_admin, get_current_admin_from_query_token, get_db

router = APIRouter()


class GfsSettings(BaseModel):
    GH: int = Field(ge=0, le=10000)
    GD: int = Field(ge=0, le=10000)
    GW: int = Field(ge=0, le=10000)
    GM: int = Field(ge=0, le=10000)
    GY: int = Field(ge=0, le=10000)


class NotifySettingsMasked(BaseModel):
    pushover_token: str = ""
    pushover_user: str = ""
    slack_url: str = ""
    smtp_host: str = ""
    smtp_port: str = "587"
    smtp_tls_mode: str = "starttls"
    smtp_from: str = ""
    smtp_user: str = ""
    smtp_pass: str = ""
    ransomware_diff_threshold: str = "500"


class TestEmailRequest(BaseModel):
    to: str


class HostEmailsUpdate(BaseModel):
    emails: list[str]


class HostCreate(BaseModel):
    host: str
    containers: list[str] = []


class ContainersUpdate(BaseModel):
    containers: list[str]


class TimerUpdate(BaseModel):
    enabled: bool


class HostConnectionTest(BaseModel):
    host: str


_STATUS_RANK = {"ransomware": 4, "failed": 3, "stale": 2, "unknown": 1, "ok": 0}


def _dataset_for(host: str, container: str) -> str:
    pool = vault_config.pool_name()
    return f"{pool}/{host.split('.')[0]}/{container}"


def _worst_status(statuses: list[str]) -> str:
    if not statuses:
        return "unknown"
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 0))


@router.get("/api/hosts")
async def list_hosts(current_user=Depends(get_current_user)):
    pool = vault_config.pool_name()
    hosts = []
    for host in vault_config.list_configured_hosts():
        containers = vault_config.read_containers(host)
        statuses = []
        last_ts = None
        for container in containers:
            dataset = _dataset_for(host, container)
            state = vault_state.read_state(dataset)
            statuses.append(vault_state.compute_status(state))
            if state and state.get("ts"):
                if last_ts is None or state["ts"] > last_ts:
                    last_ts = state["ts"]

        host_shortname = host.split(".")[0]
        pool_dataset = f"{pool}/{host_shortname}"
        hosts.append({
            "host": host,
            "host_shortname": host_shortname,
            "container_count": len(containers),
            "last_pull_ts": last_ts,
            "status": _worst_status(statuses),
            "pool_dataset": pool_dataset,
            "pool_used_bytes": vault_zfs.dataset_used_bytes(pool_dataset),
            "next_pull_epoch": vault_systemd.next_pull_time(host),
            "pull_running": vault_systemd.is_pull_running(host),
        })
    return hosts


@router.get("/api/hosts/{host}")
async def get_host_detail(host: str, current_user=Depends(get_current_user)):
    host = unquote(host)
    if host not in vault_config.list_configured_hosts():
        raise HTTPException(status_code=404, detail="Host not found")

    gfs_conf = vault_config.read_gfs_conf()
    containers = []
    for container in vault_config.read_containers(host):
        dataset = _dataset_for(host, container)
        state = vault_state.read_state(dataset)
        status = vault_state.compute_status(state)

        last_snapshot = state.get("snap") if state else None
        if not last_snapshot:
            snaps = vault_zfs.list_snapshots(dataset)
            last_snapshot = snaps[-1]["name"] if snaps else None

        containers.append({
            "name": container,
            "dataset": dataset,
            "status": status,
            "last_pull_ts": state.get("ts") if state else None,
            "last_pull_result": state.get("result") if state else None,
            "last_pull_msg": state.get("msg") if state else None,
            "last_snapshot": last_snapshot,
            "used_bytes": vault_zfs.dataset_used_bytes(dataset),
            "age_minutes": vault_state.age_minutes(state) if state else None,
            "db_backed_up": vault_zfs.has_db_dump(dataset),
            "retention": vault_zfs.snapshot_retention(dataset, gfs_conf),
            "changed_entries": vault_state.changed_entries(state),
            "ransomware_suspected": bool(state.get("ransomware_suspected")) if state else False,
        })

    return {
        "host": host,
        "host_shortname": host.split(".")[0],
        "next_pull_epoch": vault_systemd.next_pull_time(host),
        "pull_running": vault_systemd.is_pull_running(host),
        "containers": containers,
    }


@router.get("/api/hosts/{host}/containers/{container}/log")
async def get_container_pull_log(host: str, container: str, current_user=Depends(get_current_user)):
    """Detailed pull log for one container - the systemd journal for the
    MOST RECENT invocation of nspawn-vault-pull@<host>.service (see
    vault_systemd.fetch_pull_log for why that's scoped by invocation ID
    rather than a time window), since the state JSON's own "msg" field is
    only ever a short canned string (see pull.sh's fail())."""
    host = unquote(host)
    container = unquote(container)
    if host not in vault_config.list_configured_hosts():
        raise HTTPException(status_code=404, detail="Host not found")
    if container not in vault_config.read_containers(host):
        raise HTTPException(status_code=404, detail="Container not found")

    dataset = _dataset_for(host, container)
    state = vault_state.read_state(dataset)
    ts = state.get("ts") if state else None
    return {
        "unit": f"nspawn-vault-pull@{host}.service",
        "ts": ts,
        "log": vault_systemd.fetch_pull_log(host),
    }


def _validate_host_container(host: str, container: str) -> None:
    if host not in vault_config.list_configured_hosts():
        raise HTTPException(status_code=404, detail="Host not found")
    if container not in vault_config.read_containers(host):
        raise HTTPException(status_code=404, detail="Container not found")


@router.get("/api/hosts/{host}/containers/{container}/snapshots")
async def list_container_snapshots(host: str, container: str, current_user=Depends(get_current_user)):
    """Every available snapshot for one container, newest first - the
    picklist for restoring/downloading a specific point in time rather than
    just whatever the latest pull happened to produce."""
    host = unquote(host)
    container = unquote(container)
    _validate_host_container(host, container)
    dataset = _dataset_for(host, container)
    snaps = vault_zfs.list_snapshots(dataset)  # oldest-first
    return [
        {"name": s["name"].split("@", 1)[1], "creation_epoch": s["creation_epoch"]}
        for s in reversed(snaps)
    ]


def _resolve_snapshot_path(host: str, container: str, snapshot: str | None) -> tuple[str, str]:
    """Dataset -> chosen snapshot name -> its .zfs/snapshot/<name>/ dir on
    disk, shared by the archive-download, browse, and single-file-download
    endpoints. Returns (chosen_snapshot_name, absolute_path)."""
    dataset = _dataset_for(host, container)
    snaps = vault_zfs.list_snapshots(dataset)
    if not snaps:
        raise HTTPException(status_code=404, detail="No snapshots available for this container")
    snap_names = [s["name"].split("@", 1)[1] for s in snaps]
    chosen = snapshot or snap_names[-1]
    if chosen not in snap_names:
        raise HTTPException(status_code=400, detail="Unknown snapshot")

    mountpoint = vault_zfs.dataset_mountpoint(dataset)
    if not mountpoint:
        raise HTTPException(status_code=500, detail="Could not resolve dataset mountpoint")
    snap_path = os.path.join(mountpoint, ".zfs", "snapshot", chosen)
    if not os.path.isdir(snap_path):
        raise HTTPException(status_code=404, detail="Snapshot directory not found on disk")
    return chosen, snap_path


@router.get("/api/admin/hosts/{host}/containers/{container}/browse")
async def browse_snapshot(
    request: Request,
    host: str,
    container: str,
    snapshot: str | None = None,
    path: str = "",
    offset: int = 0,
    limit: int = 500,
    current_user=Depends(get_current_admin),
    db=Depends(get_db),
):
    """Lists one directory inside a chosen snapshot - a read-only file
    browser so recovering a single file doesn't require downloading (and
    decompressing) the whole container, and doesn't require shell/SSH
    access to the vault host, which the people actually using this UI may
    not have or be allowed. See vault_archive.resolve_safe_path for why
    `path` can't just be joined onto the snapshot dir without validation -
    a symlink inside the container's own filesystem could otherwise be used
    to read arbitrary files on the vault host itself."""
    host = unquote(host)
    container = unquote(container)
    _validate_host_container(host, container)
    chosen, snap_path = _resolve_snapshot_path(host, container, snapshot)

    try:
        listing = vault_archive.list_snapshot_dir(snap_path, path, offset, limit)
    except vault_archive.PathEscapeError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except NotADirectoryError as e:
        raise HTTPException(status_code=400, detail=str(e))

    vault_audit.log_action(
        db, current_user.username, "browse", host, container,
        snapshot=chosen, path=path or "/", client_ip=request.client.host if request.client else None,
    )
    return {"snapshot": chosen, "path": path, "offset": offset, "limit": limit, **listing}


@router.get("/api/admin/hosts/{host}/containers/{container}/browse-download")
async def download_snapshot_file(
    request: Request,
    host: str,
    container: str,
    path: str,
    snapshot: str | None = None,
    current_user=Depends(get_current_admin_from_query_token),
    db=Depends(get_db),
):
    """Downloads a single file out of a chosen snapshot - the query-token
    auth variant (see download_container_archive above for why), since this
    is also a native browser download rather than a fetch() call."""
    host = unquote(host)
    container = unquote(container)
    _validate_host_container(host, container)
    chosen, snap_path = _resolve_snapshot_path(host, container, snapshot)

    try:
        file_path = vault_archive.resolve_safe_path(snap_path, path)
    except vault_archive.PathEscapeError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Not a file")

    vault_audit.log_action(
        db, current_user.username, "download_file", host, container,
        snapshot=chosen, path=path, client_ip=request.client.host if request.client else None,
    )
    return FileResponse(file_path, filename=os.path.basename(file_path))


@router.get("/api/admin/hosts/{host}/containers/{container}/download")
async def download_container_archive(
    request: Request,
    host: str,
    container: str,
    snapshot: str | None = None,
    compression: str = "zstd",
    current_user=Depends(get_current_admin_from_query_token),
    db=Depends(get_db),
):
    """Streams a tar (optionally zstd/gzip-compressed) of one container's
    snapshot straight from its read-only .zfs/snapshot/<name>/ mount - never
    the whole archive materialized on disk or in memory first, since
    containers have been seen at 13GB+ in this project. Restoring the
    result onto the source host stays a deliberate manual step (scp the
    download over, extract it there) - this app never writes to a source
    host itself, only the vault's own local files (see CLAUDE.md
    Architecture section)."""
    host = unquote(host)
    container = unquote(container)
    _validate_host_container(host, container)

    if compression not in ("zstd", "gzip", "none"):
        raise HTTPException(status_code=400, detail="Invalid compression (use zstd, gzip, or none)")
    if not vault_archive.compressor_available(compression):
        raise HTTPException(status_code=500, detail=f"'{compression}' is not installed on the vault")

    chosen, snap_path = _resolve_snapshot_path(host, container, snapshot)
    filename = vault_archive.archive_filename(container, chosen, compression)
    vault_audit.log_action(
        db, current_user.username, "download_archive", host, container,
        snapshot=chosen, detail=compression, client_ip=request.client.host if request.client else None,
    )
    return StreamingResponse(
        vault_archive.stream_archive(snap_path, compression),
        media_type=vault_archive.archive_media_type(compression),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/settings/gfs")
async def get_gfs_settings(current_user=Depends(get_current_user)):
    return vault_config.read_gfs_conf()


@router.put("/api/admin/settings/gfs")
async def update_gfs_settings(data: GfsSettings, current_user=Depends(get_current_admin)):
    vault_config.write_gfs_conf(data.model_dump())
    return vault_config.read_gfs_conf()


@router.get("/api/settings/notify")
async def get_notify_settings(current_user=Depends(get_current_user)):
    return vault_config.read_notify_conf()


@router.get("/api/admin/settings/notify")
async def get_notify_settings_admin(current_user=Depends(get_current_admin)):
    """Admin-only edit view: secrets are masked with '********' when set,
    same convention as GET /api/admin/ldap - never returned in plaintext."""
    return vault_config.read_notify_conf_masked()


@router.put("/api/admin/settings/notify")
async def update_notify_settings(data: NotifySettingsMasked, current_user=Depends(get_current_admin)):
    try:
        vault_config.write_notify_conf(data.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return vault_config.read_notify_conf_masked()


@router.post("/api/admin/settings/notify/test-email")
async def test_email_admin(data: TestEmailRequest, current_user=Depends(get_current_admin)):
    """Sends one real email right now via the currently-saved SMTP relay
    config, so setting up email alerts doesn't mean waiting for a real
    dead-man's-switch trigger to find out the relay config was wrong."""
    try:
        vault_config.validate_email(data.to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return vault_email.send_test_email(data.to)


@router.get("/api/admin/hosts")
async def list_hosts_admin(current_user=Depends(get_current_admin)):
    """Configuration view (which hosts/containers are set up to be pulled,
    is the timer on) - distinct from GET /api/hosts, which is about pull
    STATUS and is available to any authenticated user."""
    hosts = []
    for host in vault_config.list_configured_hosts():
        hosts.append({
            "host": host,
            "containers": vault_config.read_containers(host),
            "emails": vault_config.read_host_emails(host),
            "timer_enabled": vault_systemd.timer_enabled(f"nspawn-vault-pull@{host}.timer"),
        })
    return hosts


@router.get("/api/admin/vault-key")
async def get_vault_public_key(current_user=Depends(get_current_admin)):
    """Not gated for any security reason - a public key has no secrecy
    requirement - just kept admin-only for consistency with the rest of the
    source-host management endpoints. Lets an admin copy it straight from
    here for a new source host's authorized_keys line, instead of SSHing
    into the vault to read /root/.ssh/nspawn-vault.pub by hand."""
    return vault_ssh.get_public_key()


@router.get("/api/admin/audit-log")
async def get_audit_log(offset: int = 0, limit: int = 100, current_user=Depends(get_current_admin), db=Depends(get_db)):
    """Who has accessed actual container data (downloads/browsing) and
    when - see models.AuditLog. Newest first."""
    return vault_audit.list_entries(db, offset, limit)


@router.post("/api/admin/hosts/test-connection")
async def test_host_connection_admin(data: HostConnectionTest, current_user=Depends(get_current_admin)):
    """Tests SSH reachability of a source host. Takes a bare hostname, not a
    reference to an already-configured host, so this works both before a
    host has been added (testing what's typed into the add-host form) and
    later against an already-configured one."""
    try:
        vault_config.validate_hostname(data.host)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return vault_ssh.test_connection(data.host)


@router.post("/api/admin/hosts", status_code=201)
async def create_host_admin(data: HostCreate, current_user=Depends(get_current_admin)):
    try:
        vault_config.create_host(data.host, data.containers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"host": data.host, "containers": data.containers, "timer_enabled": False}


@router.delete("/api/admin/hosts/{host}")
async def delete_host_admin(host: str, current_user=Depends(get_current_admin)):
    host = unquote(host)
    try:
        if vault_systemd.timer_enabled(f"nspawn-vault-pull@{host}.timer"):
            vault_systemd.disable_pull_timer(host)
        vault_config.delete_host(host)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"deleted": host}


@router.put("/api/admin/hosts/{host}/containers")
async def update_containers_admin(host: str, data: ContainersUpdate, current_user=Depends(get_current_admin)):
    host = unquote(host)
    if host not in vault_config.list_configured_hosts():
        raise HTTPException(status_code=404, detail="Host not found")
    try:
        vault_config.write_containers(host, data.containers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"host": host, "containers": data.containers}


@router.put("/api/admin/hosts/{host}/emails")
async def update_host_emails_admin(host: str, data: HostEmailsUpdate, current_user=Depends(get_current_admin)):
    """Who gets emailed (in addition to the global Pushover/Slack alerts)
    when THIS source host's dead-man's-switch fires - check-stale.sh reads
    this same file directly, not through this app."""
    host = unquote(host)
    if host not in vault_config.list_configured_hosts():
        raise HTTPException(status_code=404, detail="Host not found")
    try:
        vault_config.write_host_emails(host, data.emails)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"host": host, "emails": data.emails}


@router.put("/api/admin/hosts/{host}/timer")
async def update_timer_admin(host: str, data: TimerUpdate, current_user=Depends(get_current_admin)):
    host = unquote(host)
    if host not in vault_config.list_configured_hosts():
        raise HTTPException(status_code=404, detail="Host not found")
    try:
        if data.enabled:
            vault_systemd.enable_pull_timer(host)
        else:
            vault_systemd.disable_pull_timer(host)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"host": host, "timer_enabled": data.enabled}


@router.post("/api/admin/hosts/{host}/trigger-pull")
async def trigger_pull_admin(host: str, current_user=Depends(get_current_admin)):
    """Starts a pull for this host right now instead of waiting for its
    timer. Returns as soon as the systemd job is queued (--no-block, see
    vault_systemd.trigger_pull_now) - it does not wait for the pull itself
    to finish, which can take anywhere from seconds to over an hour."""
    host = unquote(host)
    if host not in vault_config.list_configured_hosts():
        raise HTTPException(status_code=404, detail="Host not found")
    try:
        vault_systemd.trigger_pull_now(host)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"host": host, "triggered": True}


@router.post("/api/admin/prune/trigger-now")
async def trigger_prune_admin(current_user=Depends(get_current_admin)):
    """Starts nspawn-vault-prune.service right now instead of waiting for
    its daily 04:00 timer - handy right after changing GFS settings, or to
    reclaim space proactively rather than waiting overnight. Returns as
    soon as the job is queued (--no-block), does not wait for the prune
    itself to finish."""
    try:
        vault_systemd.trigger_prune_now()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"triggered": True}


@router.get("/api/admin/prune/status")
async def prune_status_admin(current_user=Depends(get_current_admin)):
    return {"running": vault_systemd.is_prune_running()}


@router.get("/api/vault/storage")
async def get_vault_storage(current_user=Depends(get_current_user)):
    status = vault_zfs.storage_status(vault_config.pool_name())
    if status is None:
        raise HTTPException(status_code=500, detail="Could not read pool capacity")
    return status


@router.get("/api/alerts/summary")
async def get_alerts_summary(current_user=Depends(get_current_user)):
    stale_hosts = []
    failed_hosts = []
    running_hosts = []
    ransomware_hosts = []
    for host in vault_config.list_configured_hosts():
        if vault_systemd.is_pull_running(host):
            # A fresh pull is already in progress for this host right now -
            # don't alarm about its last recorded result (which could still
            # say "failed" from before this attempt started; the state JSON
            # only gets overwritten once the pull finishes). Surfaced
            # separately below instead, as a calmer "in progress" signal.
            running_hosts.append(host)
            continue
        statuses = []
        for container in vault_config.read_containers(host):
            dataset = _dataset_for(host, container)
            statuses.append(vault_state.compute_status(vault_state.read_state(dataset)))
        worst = _worst_status(statuses)
        if worst == "ransomware":
            ransomware_hosts.append(host)
        elif worst == "failed":
            failed_hosts.append(host)
        elif worst == "stale":
            stale_hosts.append(host)
    zfs_status = vault_zfs.module_status()
    storage_status = vault_zfs.storage_status(vault_config.pool_name())
    storage_ok = storage_status is None or storage_status["ok"]
    return {
        "stale_hosts": stale_hosts,
        "failed_hosts": failed_hosts,
        "running_hosts": running_hosts,
        "ransomware_hosts": ransomware_hosts,
        "zfs_module_status": zfs_status,
        "storage_status": storage_status,
        "has_alert": bool(stale_hosts or failed_hosts or ransomware_hosts or not zfs_status["ok"] or not storage_ok),
    }
