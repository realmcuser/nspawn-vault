import os
import re
import shutil
from pathlib import Path

NSPAWN_VAULT_ETC = Path("/etc/nspawn-vault")

_GFS_DEFAULTS = {"GH": 24, "GD": 7, "GW": 4, "GM": 12, "GY": 3}
_KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*(#.*)?$")

# Host becomes a directory name (NSPAWN_VAULT_ETC / host) and container names
# become lines in a file plus ZFS dataset path segments and remote SSH
# command arguments (see nspawn-pull's dispatch.sh on the source host) - both
# must be validated strictly against path traversal / shell/ZFS-unsafe
# characters before ever touching the filesystem.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# Deliberately simple (not RFC 5322) - this only needs to reject anything that
# could act as a curl/shell option or metacharacter when notify-email lines
# are later passed to send-email.sh's --mail-rcpt, not validate every
# technically-legal email address.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def validate_hostname(host: str) -> None:
    if not _HOSTNAME_RE.match(host):
        raise ValueError(f"invalid hostname: {host!r}")


def validate_container_name(name: str) -> None:
    if not _CONTAINER_NAME_RE.match(name):
        raise ValueError(f"invalid container name: {name!r}")


def validate_email(address: str) -> None:
    if not _EMAIL_RE.match(address):
        raise ValueError(f"invalid email address: {address!r}")


def _atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    """Writes via a temp file in the same directory + os.replace(), instead
    of Path.write_text() straight onto the target - write_text() truncates
    the existing file in place before writing the new content, so a
    concurrent reader (check-stale.sh's 30-minute timer sourcing
    notify.conf, or someone clicking "send test email" right as a save is
    in flight) has a real, if narrow, window to read a half-written file.
    os.replace() is an atomic rename on the same filesystem - a reader
    always sees either the complete old file or the complete new one.
    `mode` is applied to the temp file *before* the rename so the target
    never briefly exists with the wrong (looser) permissions."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _parse_shell_kv(path: Path) -> dict:
    """Extracts KEY=VALUE pairs from a shell-sourceable config file without
    executing it — these files are trusted content but we still don't want to
    `source` arbitrary shell as root just to read a few variables."""
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _KV_RE.match(line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return values


def read_containers(host: str) -> list[str]:
    """Mirrors pull-host.sh's own filter: skip blank lines and lines starting with #."""
    path = NSPAWN_VAULT_ETC / host / "containers"
    if not path.is_file():
        return []
    names = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        names.append(stripped)
    return names


def read_host_emails(host: str) -> list[str]:
    """Per-host email alert recipients (separate from the global
    Pushover/Slack config in notify.conf) - one address per line in
    <host>/notify-email, mirrors read_containers()'s own format/filtering."""
    path = NSPAWN_VAULT_ETC / host / "notify-email"
    if not path.is_file():
        return []
    addrs = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        addrs.append(stripped)
    return addrs


def write_host_emails(host: str, addresses: list[str]) -> None:
    validate_hostname(host)
    for addr in addresses:
        validate_email(addr)
    path = NSPAWN_VAULT_ETC / host / "notify-email"
    if not path.parent.is_dir():
        raise ValueError(f"host not found: {host}")
    text = "".join(f"{a}\n" for a in addresses)
    _atomic_write_text(path, text)


def list_configured_hosts() -> list[str]:
    if not NSPAWN_VAULT_ETC.is_dir():
        return []
    hosts = []
    for entry in NSPAWN_VAULT_ETC.iterdir():
        if entry.is_dir() and (entry / "containers").is_file():
            hosts.append(entry.name)
    return sorted(hosts)


def write_containers(host: str, containers: list[str]) -> None:
    validate_hostname(host)
    for c in containers:
        validate_container_name(c)
    path = NSPAWN_VAULT_ETC / host / "containers"
    if not path.parent.is_dir():
        raise ValueError(f"host not found: {host}")
    text = "".join(f"{c}\n" for c in containers)
    _atomic_write_text(path, text)


def create_host(host: str, containers: list[str]) -> None:
    validate_hostname(host)
    for c in containers:
        validate_container_name(c)
    host_dir = NSPAWN_VAULT_ETC / host
    if host_dir.exists():
        raise ValueError(f"host already configured: {host}")
    host_dir.mkdir(parents=True)
    write_containers(host, containers)


def delete_host(host: str) -> None:
    """Removes the pull configuration only - does NOT touch ZFS datasets or
    snapshots. Existing backups for this host are never deleted by this app."""
    validate_hostname(host)
    host_dir = NSPAWN_VAULT_ETC / host
    if not host_dir.is_dir():
        raise ValueError(f"host not found: {host}")
    shutil.rmtree(host_dir)


def read_gfs_conf() -> dict:
    values = _parse_shell_kv(NSPAWN_VAULT_ETC / "gfs.conf")
    result = dict(_GFS_DEFAULTS)
    for key in result:
        if key in values:
            try:
                result[key] = int(values[key])
            except ValueError:
                pass
    return result


def write_gfs_conf(values: dict) -> None:
    """values must have GH/GD/GW/GM/GY int keys - validated by the caller
    (Pydantic schema) before this ever runs."""
    lines = [
        "# GFS-retention for ZFS snapshots on the vault - managed via nspawn-vault-web Admin",
        f"GH={int(values['GH'])}",
        f"GD={int(values['GD'])}",
        f"GW={int(values['GW'])}",
        f"GM={int(values['GM'])}",
        f"GY={int(values['GY'])}",
        "",
    ]
    path = NSPAWN_VAULT_ETC / "gfs.conf"
    _atomic_write_text(path, "\n".join(lines))


SECRET_SENTINEL = "********"


def _shell_quote(value: str) -> str:
    """check-stale.sh does `source "$NOTIFY_CONF"` as root - any value written
    here must be safe against shell metacharacters ($, `, ;, ...) or a stray
    character in a token could execute arbitrary code next time the
    dead-man's-switch timer fires. Single-quote, escaping embedded quotes."""
    return "'" + value.replace("'", "'\\''") + "'"


def read_notify_conf() -> dict:
    values = _parse_shell_kv(NSPAWN_VAULT_ETC / "notify.conf")
    try:
        ransomware_threshold = int(values.get("RANSOMWARE_DIFF_THRESHOLD", 500))
    except ValueError:
        ransomware_threshold = 500
    try:
        alert_backoff_hours = int(values.get("ALERT_BACKOFF_HOURS", 6))
    except ValueError:
        alert_backoff_hours = 6
    return {
        "pushover_configured": bool(values.get("PUSHOVER_TOKEN")) and bool(values.get("PUSHOVER_USER")),
        "slack_configured": bool(values.get("SLACK_URL")),
        "smtp_configured": bool(values.get("SMTP_HOST")),
        "ransomware_diff_threshold": ransomware_threshold,
        "alert_backoff_hours": alert_backoff_hours,
    }


def read_notify_conf_masked() -> dict:
    """For the admin edit form: never return real secret values, only a
    sentinel when something is already set (same pattern as LDAP bind_password).
    SMTP_HOST/PORT/TLS_MODE/FROM are not secrets and come back as-is; only
    SMTP_USER/SMTP_PASS follow the sentinel convention (a relay username can
    be sensitive enough to not want it echoed back either)."""
    values = _parse_shell_kv(NSPAWN_VAULT_ETC / "notify.conf")
    return {
        "pushover_token": SECRET_SENTINEL if values.get("PUSHOVER_TOKEN") else "",
        "pushover_user": SECRET_SENTINEL if values.get("PUSHOVER_USER") else "",
        "slack_url": SECRET_SENTINEL if values.get("SLACK_URL") else "",
        "smtp_host": values.get("SMTP_HOST", ""),
        "smtp_port": values.get("SMTP_PORT", "587"),
        "smtp_tls_mode": values.get("SMTP_TLS_MODE", "starttls"),
        "smtp_from": values.get("SMTP_FROM", ""),
        "smtp_user": SECRET_SENTINEL if values.get("SMTP_USER") else "",
        "smtp_pass": SECRET_SENTINEL if values.get("SMTP_PASS") else "",
        "ransomware_diff_threshold": values.get("RANSOMWARE_DIFF_THRESHOLD", "500"),
        "alert_backoff_hours": values.get("ALERT_BACKOFF_HOURS", "6"),
    }


def write_notify_conf(values: dict) -> None:
    """Only overwrites fields whose incoming value is not the redaction
    sentinel - an admin who didn't touch a field in the form must not blank
    out (or corrupt) the real stored secret."""
    current = _parse_shell_kv(NSPAWN_VAULT_ETC / "notify.conf")

    def resolve(new_value, existing_key):
        if new_value == SECRET_SENTINEL:
            return current.get(existing_key, "")
        return new_value or ""

    token = resolve(values.get("pushover_token"), "PUSHOVER_TOKEN")
    user = resolve(values.get("pushover_user"), "PUSHOVER_USER")
    slack = resolve(values.get("slack_url"), "SLACK_URL")
    smtp_user = resolve(values.get("smtp_user"), "SMTP_USER")
    smtp_pass = resolve(values.get("smtp_pass"), "SMTP_PASS")

    ransomware_threshold = str(values.get("ransomware_diff_threshold") or "500").strip()
    if not ransomware_threshold.isdigit():
        raise ValueError(f"invalid ransomware diff threshold: {ransomware_threshold!r}")

    alert_backoff_hours = str(values.get("alert_backoff_hours") or "6").strip()
    if not alert_backoff_hours.isdigit():
        raise ValueError(f"invalid alert backoff hours: {alert_backoff_hours!r}")

    lines = [
        "# Pushover for the dead-man's-switch alert - managed via nspawn-vault-web Admin",
        f"PUSHOVER_TOKEN={_shell_quote(token)}",
        f"PUSHOVER_USER={_shell_quote(user)}",
        "# Slack webhook (optional)",
        f"SLACK_URL={_shell_quote(slack)}",
        # check-stale.sh `source`s this file as root - every value below MUST
        # go through _shell_quote(), same as the fields above (gotcha #1).
        "# SMTP relay for email alerts (optional) - who actually gets emailed",
        "# per source host is in <host>/notify-email, not here.",
        f"SMTP_HOST={_shell_quote(values.get('smtp_host') or '')}",
        f"SMTP_PORT={_shell_quote(str(values.get('smtp_port') or '587'))}",
        f"SMTP_TLS_MODE={_shell_quote(values.get('smtp_tls_mode') or 'starttls')}",
        f"SMTP_FROM={_shell_quote(values.get('smtp_from') or '')}",
        f"SMTP_USER={_shell_quote(smtp_user)}",
        f"SMTP_PASS={_shell_quote(smtp_pass)}",
        "# Ransomware-heuristik (0 = av) - se notify.conf.example för detaljer",
        f"RANSOMWARE_DIFF_THRESHOLD={_shell_quote(ransomware_threshold)}",
        "# Upprepningsspärr för larm, timmar (0 = larma varje check-stale.sh-körning)",
        f"ALERT_BACKOFF_HOURS={_shell_quote(alert_backoff_hours)}",
        "",
    ]
    path = NSPAWN_VAULT_ETC / "notify.conf"
    _atomic_write_text(path, "\n".join(lines), mode=0o600)


def pool_name() -> str:
    return os.environ.get("NSPAWN_VAULT_POOL", "vault")
