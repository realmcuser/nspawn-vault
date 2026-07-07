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


def validate_hostname(host: str) -> None:
    if not _HOSTNAME_RE.match(host):
        raise ValueError(f"invalid hostname: {host!r}")


def validate_container_name(name: str) -> None:
    if not _CONTAINER_NAME_RE.match(name):
        raise ValueError(f"invalid container name: {name!r}")


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
    path.write_text(text)


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
    path.write_text("\n".join(lines))


SECRET_SENTINEL = "********"


def _shell_quote(value: str) -> str:
    """check-stale.sh does `source "$NOTIFY_CONF"` as root - any value written
    here must be safe against shell metacharacters ($, `, ;, ...) or a stray
    character in a token could execute arbitrary code next time the
    dead-man's-switch timer fires. Single-quote, escaping embedded quotes."""
    return "'" + value.replace("'", "'\\''") + "'"


def read_notify_conf() -> dict:
    values = _parse_shell_kv(NSPAWN_VAULT_ETC / "notify.conf")
    return {
        "pushover_configured": bool(values.get("PUSHOVER_TOKEN")) and bool(values.get("PUSHOVER_USER")),
        "slack_configured": bool(values.get("SLACK_URL")),
    }


def read_notify_conf_masked() -> dict:
    """For the admin edit form: never return real secret values, only a
    sentinel when something is already set (same pattern as LDAP bind_password)."""
    values = _parse_shell_kv(NSPAWN_VAULT_ETC / "notify.conf")
    return {
        "pushover_token": SECRET_SENTINEL if values.get("PUSHOVER_TOKEN") else "",
        "pushover_user": SECRET_SENTINEL if values.get("PUSHOVER_USER") else "",
        "slack_url": SECRET_SENTINEL if values.get("SLACK_URL") else "",
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

    lines = [
        "# Pushover for the dead-man's-switch alert - managed via nspawn-vault-web Admin",
        f"PUSHOVER_TOKEN={_shell_quote(token)}",
        f"PUSHOVER_USER={_shell_quote(user)}",
        "# Slack webhook (optional)",
        f"SLACK_URL={_shell_quote(slack)}",
        "",
    ]
    path = NSPAWN_VAULT_ETC / "notify.conf"
    path.write_text("\n".join(lines))
    os.chmod(path, 0o600)


def pool_name() -> str:
    return os.environ.get("NSPAWN_VAULT_POOL", "vault")
