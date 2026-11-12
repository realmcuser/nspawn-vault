import re
import subprocess
from pathlib import Path

SSH_KEY = "/root/.ssh/nspawn-vault"
SSH_PUBLIC_KEY = Path(f"{SSH_KEY}.pub")
CONNECT_TIMEOUT = 10

# Matches source-host/README.md's install path exactly - this is what the
# forced-command authorized_keys line restricts the vault's key to.
DISPATCH_SCRIPT_PATH = "/usr/local/lib/nspawn-pull/dispatch.sh"

NSPAWN_VAULT_ETC = Path("/etc/nspawn-vault")

# Absolute path, conservative character set (no spaces/quotes/shell
# metacharacters) - this ends up inside a forced-command string an admin
# copy-pastes onto a source host's authorized_keys, so it must be
# impossible to break out of the surrounding double quotes even by
# accident, not just "probably fine".
_EXTRA_PATH_RE = re.compile(r'^/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+$')


def get_public_key() -> dict:
    """The vault's own public SSH key - not a secret (that's the whole point
    of public-key crypto; only the private half at SSH_KEY needs protecting),
    so this is safe to expose to any admin.

    Returns two forms, for two different real paste targets:
    - "key": the bare public key, for cockpit-nspawn's own "Enable pull
      backup" toggle (nspawn-cockpit's PULL-BACKUP-INTEGRATION.md), which
      constructs the authorized_keys line itself - pasting the already-
      wrapped line into that field would double-wrap it.
    - "authorized_keys_line": the full, pre-built line
      (restrict,command="..." + the key), for directly editing a source
      host's /root/.ssh/authorized_keys by hand when the toggle isn't
      used. Handing out just the bare key for this path is exactly what
      led to a source host with a fully unrestricted root login for the
      vault's key instead of the intended dispatch.sh-only access - found
      live 2026-10-08 on a manually-onboarded host (ljan9.vpn.fhd.se)."""
    if not SSH_PUBLIC_KEY.is_file():
        return {"exists": False, "key": None, "authorized_keys_line": None}
    key = SSH_PUBLIC_KEY.read_text().strip()
    return {
        "exists": True,
        "key": key,
        "authorized_keys_line": f'restrict,command="{DISPATCH_SCRIPT_PATH}" {key}',
    }


def test_connection(host: str) -> dict:
    """Tests whether the vault can reach a pull source host over SSH.

    Source hosts force every session through their own dispatch.sh (see
    engine/README.md) - whatever command we send is ignored server-side in
    favor of the forced one. That means ssh's own exit code still tells us
    what we need: 255 is ssh's own transport/auth failure (per ssh(1)),
    while any other code means the connection + key auth succeeded and
    dispatch.sh ran (even though it rejects our arbitrary probe command) -
    which is exactly what this test wants to confirm. Caller must validate
    `host` (e.g. vault_config.validate_hostname) before calling this -  it
    is passed as a literal argv element to ssh, never through a shell.
    """
    try:
        proc = subprocess.run(
            [
                "ssh", "-i", SSH_KEY,
                "-o", "BatchMode=yes",
                "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
                "-o", "StrictHostKeyChecking=accept-new",
                host, "true",
            ],
            capture_output=True, text=True, timeout=CONNECT_TIMEOUT + 5,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"Ingen anslutning inom {CONNECT_TIMEOUT} sekunder (timeout).",
        }

    stderr = proc.stderr.strip()
    if proc.returncode == 255:
        return {
            "success": False,
            "message": stderr or "SSH-anslutningen misslyckades.",
        }
    return {
        "success": True,
        "message": "SSH-anslutningen fungerar - nyckeln accepterades av källservern.",
        "detail": stderr or None,
    }


def create_extra_path_key(host: str, label: str, remote_path: str) -> dict:
    """Generates a new, dedicated ed25519 keypair for one "extra path" (see
    engine/usr/libexec/nspawn-vault/pull-extra.sh and
    bind-mounted-storage.md) - a host-level directory outside any
    container's own rootfs, typically data a container only sees via a
    systemd-nspawn Bind= mount, invisible to a normal pull.sh run.

    Deliberately a SEPARATE key per extra path, never the vault's shared
    SSH_KEY: a forced command restricted to `rrsync -ro <one exact path>`
    means a compromised key only ever exposes that one directory - unlike
    SSH_KEY/get_public_key() above, which is intentionally shared across
    every container on a host because dispatch.sh itself (not the key) is
    that confinement boundary.

    Returns the authorized_keys line to install on the SOURCE host - same
    as get_public_key()'s authorized_keys_line, this process never touches
    the source host itself, it only hands back what an admin pastes there
    by hand. Raises ValueError on a bad path or an already-existing label -
    callers should map that to a 400."""
    if not _EXTRA_PATH_RE.match(remote_path):
        raise ValueError(f"invalid path: {remote_path!r}")
    extra_dir = NSPAWN_VAULT_ETC / host / "extra"
    extra_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = extra_dir / f"{label}.key"
    if key_path.exists():
        raise ValueError(f"extra path already exists: {label}")
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path),
         "-C", f"nspawn-vault-extra:{label}"],
        check=True, capture_output=True, text=True,
    )
    key_path.chmod(0o600)
    pub_path = key_path.parent / f"{key_path.name}.pub"
    pub_path.chmod(0o644)
    pubkey = pub_path.read_text().strip()
    return {
        "label": label,
        "authorized_keys_line": f'restrict,command="rrsync -ro {remote_path}" {pubkey}',
    }


def test_extra_path_connection(host: str, label: str) -> dict:
    """Same exit-code logic as test_connection() above, but the forced
    command here is a bare `rrsync -ro <path>` instead of dispatch.sh - a
    non-rsync probe command gets rejected by rrsync ITSELF (not ssh), which
    is exactly what proves the key and its restriction are correctly
    installed. Confirmed live: a bare `ssh -i <extra key> host true` against
    a correctly-installed line returns
    "/usr/local/bin/rrsync error: SSH_ORIGINAL_COMMAND does not run rsync",
    exit 1 - still "anything other than 255", the same success signal
    test_connection() relies on."""
    key_path = NSPAWN_VAULT_ETC / host / "extra" / f"{label}.key"
    if not key_path.is_file():
        return {
            "success": False,
            "message": "Nyckeln hittades inte - inget extra path med den etiketten är konfigurerat.",
        }
    try:
        proc = subprocess.run(
            [
                "ssh", "-i", str(key_path),
                "-o", "BatchMode=yes",
                "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
                "-o", "StrictHostKeyChecking=accept-new",
                host, "true",
            ],
            capture_output=True, text=True, timeout=CONNECT_TIMEOUT + 5,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"Ingen anslutning inom {CONNECT_TIMEOUT} sekunder (timeout).",
        }

    stderr = proc.stderr.strip()
    if proc.returncode == 255:
        return {
            "success": False,
            "message": stderr or "SSH-anslutningen misslyckades - är authorized_keys-raden inklistrad på källservern?",
        }
    return {
        "success": True,
        "message": "SSH-anslutningen fungerar - nyckeln accepterades och är begränsad till exakt den här sökvägen.",
        "detail": stderr or None,
    }
