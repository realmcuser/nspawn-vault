import re
import shlex
import subprocess


def next_pull_time(host: str) -> int | None:
    """Epoch seconds of the next scheduled run for nspawn-vault-pull@<host>.timer,
    or None if the timer isn't enabled/found. Some systemd versions (e.g. 252 on
    AlmaLinux 9) return a human-readable NextElapseUSecRealtime string instead of
    raw microseconds — same conversion already working in
    /root/nspawn-cockpit/src/BackupsOverview.jsx, adapted here."""
    unit = f"nspawn-vault-pull@{host}.timer"
    # NB: `date -d ""` does NOT fail — it silently parses to "today" instead
    # of erroring, so a missing/unscheduled timer (empty NextElapseUSecRealtime)
    # must be checked explicitly before ever calling `date -d`, or this
    # returns a bogus-but-valid epoch instead of falling through to 0.
    cmd = (
        f"ts=$(systemctl show --property=NextElapseUSecRealtime --value {shlex.quote(unit)} 2>/dev/null); "
        f'if [ -z "$ts" ]; then echo 0; else date -d "$ts" +%s 2>/dev/null || echo 0; fi'
    )
    proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
    try:
        secs = int(proc.stdout.strip() or 0)
    except ValueError:
        return None
    return secs if secs > 0 else None


def timer_enabled(unit: str) -> bool:
    proc = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True, timeout=5)
    return proc.returncode == 0


def is_pull_running(host: str) -> bool:
    """Whether nspawn-vault-pull@<host>.service is actively executing right
    now, straight from systemd's own live state - NOT from the state JSON,
    which only gets (over)written once a pull finishes and stays on its
    previous result (e.g. "failed") for the entire duration of a fresh
    attempt. A oneshot service like this one stays "activating" for as long
    as pull.sh/pull-host.sh's process tree is running, only becoming
    "inactive" once it exits - so "activating" here means "still running",
    not "about to start"."""
    unit = f"nspawn-vault-pull@{host}.service"
    proc = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5)
    return proc.stdout.strip() in ("active", "activating")


def enable_pull_timer(host: str) -> None:
    unit = f"nspawn-vault-pull@{host}.timer"
    proc = subprocess.run(["systemctl", "enable", "--now", unit], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"systemctl enable --now {unit} failed")


def disable_pull_timer(host: str) -> None:
    unit = f"nspawn-vault-pull@{host}.timer"
    proc = subprocess.run(["systemctl", "disable", "--now", unit], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"systemctl disable --now {unit} failed")


def trigger_pull_now(host: str) -> None:
    """Starts nspawn-vault-pull@<host>.service immediately instead of
    waiting for its timer. `--no-block` is essential here - a first full
    pull can take a long time (an hour+ for a large container, seen live),
    and this call is made from an HTTP request handler that must return
    quickly. systemd itself guarantees only one instance of this unit name
    runs at a time, so calling this while a pull is already in progress for
    this host is a safe no-op, not a duplicate run."""
    unit = f"nspawn-vault-pull@{host}.service"
    proc = subprocess.run(["systemctl", "start", "--no-block", unit], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"systemctl start {unit} failed")


def is_prune_running() -> bool:
    """Same live-state check as is_pull_running(), but for the single
    global nspawn-vault-prune.service (GFS retention runs across every
    host/container in one pass, not per-host)."""
    proc = subprocess.run(
        ["systemctl", "is-active", "nspawn-vault-prune.service"],
        capture_output=True, text=True, timeout=5,
    )
    return proc.stdout.strip() in ("active", "activating")


def trigger_prune_now() -> None:
    """Starts nspawn-vault-prune.service immediately instead of waiting
    for its daily 04:00 timer - same --no-block reasoning as
    trigger_pull_now(): this runs from an HTTP handler that must return
    quickly, and systemd already guarantees only one instance of this
    unit runs at a time, so calling this while a prune is already in
    progress is a safe no-op."""
    proc = subprocess.run(
        ["systemctl", "start", "--no-block", "nspawn-vault-prune.service"],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "systemctl start nspawn-vault-prune.service failed")


# pull-host.sh prints "--- <name> ---" before pulling each container in a
# host's run, and pull.sh itself prints "=== Pull <name> from <host> ==="
# right after - either works as a block boundary, but "--- name ---" is
# the one that's always present even when pull.sh dies before its own
# first line (e.g. a bad $STATE_DIR). journalctl -o short-iso prefixes
# every line with a timestamp/hostname/unit[pid], hence the leading ".*".
_CONTAINER_MARKER = re.compile(r'^.*--- (\S+) ---\s*$', re.MULTILINE)


def _slice_container_block(log: str, container: str) -> str:
    """Cuts the shared per-host journal text down to just one container's
    block - fetch_pull_log() otherwise returns the ENTIRE host's run
    (every container pulled in that invocation), which is genuinely
    confusing when read from the "view log" button on one specific
    container (confirmed confusing Johan live, 2026-08-03: a failed
    fhdcore-jf2 pull's log also showed samba-fhdcore-jf2's unrelated
    lines). Falls back to the full log if the marker isn't found (e.g. an
    older/already-rotated-out journal predating this slicing) rather than
    returning nothing."""
    for m in _CONTAINER_MARKER.finditer(log):
        if m.group(1) == container:
            next_m = _CONTAINER_MARKER.search(log, m.end())
            end = next_m.start() if next_m else len(log)
            return log[m.start():end].strip()
    return log


def fetch_pull_log(host: str, container: str | None = None, max_lines: int = 2000) -> str:
    """Journal for the MOST RECENT invocation of
    nspawn-vault-pull@<host>.service only - the systemd unit
    pull.sh/pull-host.sh's stdout+stderr is captured under (StandardOutput/
    StandardError=journal), and it's the only place the real rsync/ssh error
    text lives (the state JSON's own "msg" field, see pull.sh's fail(), is
    just a short canned string).

    Scoped via systemd's own per-activation _SYSTEMD_INVOCATION_ID rather
    than a time window (an earlier version used +/-15 minutes around the
    state's own "ts") - a fixed time window risks blending in an adjacent
    run's lines whenever pulls happen close together (repeated manual
    "run now" triggers, or a fast retry right after a failure - both hit
    live while testing this), which made it impossible to tell whether the
    log shown was actually current. Each `systemctl start` gets its own
    invocation ID, so this always returns exactly one run's output, never
    mixed with an older one. `-o short-iso` adds a real timestamp to every
    line for the same reason.

    The unit is still shared across every container pulled from this host
    in one run - pass `container` to slice the returned text down to just
    that container's own block via _slice_container_block(); omit it (or
    pass None) to get the raw, unsliced per-host text as before."""
    unit = f"nspawn-vault-pull@{host}.service"
    inv_id = subprocess.run(
        ["systemctl", "show", unit, "--property=InvocationID", "--value"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()

    cmd = ["journalctl", "--no-pager", "-o", "short-iso"]
    if inv_id and inv_id.strip("0"):
        cmd += [f"_SYSTEMD_INVOCATION_ID={inv_id}"]
    else:
        # Never started, or systemd has already forgotten the invocation ID -
        # fall back to this unit's own recent history rather than nothing.
        cmd += ["-u", unit, "-n", str(max_lines)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    full = proc.stdout or proc.stderr or ""
    return _slice_container_block(full, container) if container else full
