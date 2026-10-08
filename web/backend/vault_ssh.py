import subprocess
from pathlib import Path

SSH_KEY = "/root/.ssh/nspawn-vault"
SSH_PUBLIC_KEY = Path(f"{SSH_KEY}.pub")
CONNECT_TIMEOUT = 10

# Matches source-host/README.md's install path exactly - this is what the
# forced-command authorized_keys line restricts the vault's key to.
DISPATCH_SCRIPT_PATH = "/usr/local/lib/nspawn-pull/dispatch.sh"


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
