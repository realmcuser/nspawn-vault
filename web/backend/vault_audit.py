from datetime import datetime, timezone

import models


def log_action(
    db,
    username: str,
    action: str,
    host: str,
    container: str,
    snapshot: str | None = None,
    path: str | None = None,
    detail: str | None = None,
    client_ip: str | None = None,
) -> None:
    """Records one access to actual container data (see models.AuditLog for
    why this exists). Called after request validation succeeds but before
    the response actually starts streaming - if a client disconnects
    mid-download that's a networking concern, not a reason to pretend the
    admin never initiated the access in the first place."""
    entry = models.AuditLog(
        timestamp=datetime.now(timezone.utc).isoformat(),
        username=username,
        action=action,
        host=host,
        container=container,
        snapshot=snapshot,
        path=path,
        detail=detail,
        client_ip=client_ip,
    )
    db.add(entry)
    db.commit()


def list_entries(db, offset: int = 0, limit: int = 100) -> dict:
    total = db.query(models.AuditLog).count()
    rows = (
        db.query(models.AuditLog)
        .order_by(models.AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    entries = [
        {
            "timestamp": r.timestamp,
            "username": r.username,
            "action": r.action,
            "host": r.host,
            "container": r.container,
            "snapshot": r.snapshot,
            "path": r.path,
            "detail": r.detail,
            "client_ip": r.client_ip,
        }
        for r in rows
    ]
    return {"entries": entries, "total": total, "offset": offset, "limit": limit}
