import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

# Source of truth: /usr/libexec/nspawn-vault/check-stale.sh, THRESHOLD_MIN=180.
# Keep these two definitions of "stale" in sync — see Phase 9 cross-check.
THRESHOLD_MIN = 180

STATE_DIR = Path("/var/lib/nspawn-vault/state")

Status = Literal["ok", "stale", "failed", "unknown"]


def state_file_for(dataset: str) -> Path:
    """Exact port of pull.sh: STATE="$STATE_DIR/${DATASET//\\/_}.json" """
    return STATE_DIR / (dataset.replace("/", "_") + ".json")


def read_state(dataset: str) -> Optional[dict]:
    path = state_file_for(dataset)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def age_minutes(state: dict) -> Optional[float]:
    ts = state.get("ts")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    now = datetime.now(dt.tzinfo or timezone.utc)
    return (now - dt).total_seconds() / 60


def compute_status(state: Optional[dict]) -> Status:
    if state is None:
        return "unknown"
    result = state.get("result", "missing")
    if result != "success":
        return "failed"
    age = age_minutes(state)
    if age is None or age > THRESHOLD_MIN:
        return "stale"
    return "ok"


def is_alerting(status: Status) -> bool:
    """Matches check-stale.sh's errors += 1 condition exactly."""
    return status in ("stale", "failed")
