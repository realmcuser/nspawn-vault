import os
import subprocess

SEND_EMAIL_SCRIPT = "/usr/libexec/nspawn-vault/send-email.sh"
TIMEOUT = 20


def send_test_email(to: str) -> dict:
    """Synchronously sends one real test email via the same script
    check-stale.sh uses for its batched per-host alerts - a successful
    result here means the SMTP relay config actually works end to end, not
    just that the form was filled in. Mirrors vault_ssh.test_connection's
    result shape ({success, message[, detail]})."""
    if not os.path.isfile(SEND_EMAIL_SCRIPT):
        return {
            "success": False,
            "message": "send-email.sh saknas - är nspawn-vault (engine) installerat på den här värden?",
        }

    try:
        proc = subprocess.run(
            [
                SEND_EMAIL_SCRIPT, to,
                "nspawn-vault: testmejl",
                "Detta är ett testmejl från nspawn-vault-web:s Admin-sida. "
                "Om du läser detta fungerar SMTP-utskicket.",
            ],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"Ingen respons från SMTP-reläet inom {TIMEOUT} sekunder (timeout).",
        }

    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        return {
            "success": False,
            "message": stderr or f"send-email.sh misslyckades (exit {proc.returncode}).",
        }
    return {
        "success": True,
        "message": f"Testmejl skickat till {to}.",
        "detail": stderr or None,
    }
