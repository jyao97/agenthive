"""Push notification routes — VAPID key, subscribe, unsubscribe."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db

logger = logging.getLogger("orchestrator")

router = APIRouter(prefix="/api/push", tags=["push"])

# A subscription is pruned if it has never acked AND was created more
# than ZOMBIE_AFTER_DAYS ago, OR if its last ack is older than that.
ZOMBIE_AFTER_DAYS = 14


def prune_zombie_subscriptions(db: Session) -> int:
    """Delete push subs that haven't ACK'd within the zombie window.

    A freshly-registered sub is kept during the grace period even if
    it hasn't acked yet — the first push it's included in will tell
    us whether it's alive.  Returns the count deleted.
    """
    from models import PushSubscription

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=ZOMBIE_AFTER_DAYS)
    stale = db.query(PushSubscription).filter(
        or_(
            PushSubscription.last_ack_at < cutoff,
            (PushSubscription.last_ack_at.is_(None))
            & (PushSubscription.created_at < cutoff),
        )
    ).all()
    if not stale:
        return 0
    ids = [s.id for s in stale]
    for s in stale:
        logger.info(
            "push prune: removing zombie sub=%s last_ack=%s created=%s endpoint=%s…",
            s.id, s.last_ack_at, s.created_at, (s.endpoint or "")[:50],
        )
    db.query(PushSubscription).filter(
        PushSubscription.id.in_(ids)
    ).delete(synchronize_session=False)
    db.commit()
    return len(ids)


@router.get("/vapid-public-key")
async def push_vapid_public_key():
    """Return the VAPID public key for Web Push subscription."""
    from config import VAPID_PUBLIC_KEY
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="VAPID keys not configured")
    return {"publicKey": VAPID_PUBLIC_KEY}


@router.post("/subscribe")
async def push_subscribe(request: Request, db: Session = Depends(get_db)):
    """Register a push subscription (upsert by endpoint)."""
    from models import PushSubscription

    body = await request.json()
    endpoint = body.get("endpoint", "")
    keys = body.get("keys", {})
    p256dh = keys.get("p256dh", "")
    auth = keys.get("auth", "")

    if not endpoint or not p256dh or not auth:
        raise HTTPException(status_code=400, detail="Missing endpoint or keys")

    existing = db.query(PushSubscription).filter(
        PushSubscription.endpoint == endpoint
    ).first()
    if existing:
        existing.p256dh_key = p256dh
        existing.auth_key = auth
        logger.info("push/subscribe: updated existing subscription (endpoint=%s…)", endpoint[:60])
    else:
        db.add(PushSubscription(
            endpoint=endpoint,
            p256dh_key=p256dh,
            auth_key=auth,
        ))
        logger.info("push/subscribe: registered new subscription (endpoint=%s…)", endpoint[:60])
    db.commit()
    # Opportunistic zombie sweep: any time a real client subscribes,
    # try to prune subs that haven't acked in the zombie window.
    try:
        pruned = prune_zombie_subscriptions(db)
        if pruned:
            logger.info("push/subscribe: pruned %d zombie subscriptions", pruned)
    except Exception:
        logger.exception("push/subscribe: prune failed (non-fatal)")
    total = db.query(PushSubscription).count()
    logger.info("push/subscribe: total active subscriptions = %d", total)
    return {"status": "subscribed"}


@router.post("/unsubscribe")
async def push_unsubscribe(request: Request, db: Session = Depends(get_db)):
    """Remove a push subscription by endpoint."""
    from models import PushSubscription

    body = await request.json()
    endpoint = body.get("endpoint", "")
    if not endpoint:
        raise HTTPException(status_code=400, detail="Missing endpoint")

    db.query(PushSubscription).filter(
        PushSubscription.endpoint == endpoint
    ).delete(synchronize_session=False)
    db.commit()
    return {"status": "unsubscribed"}


@router.post("/ack")
async def push_ack(request: Request, db: Session = Depends(get_db)):
    """Diagnostic: SW posts here from its push handler so we can tell
    'push reached device' apart from 'push never arrived'.

    Body: {nid, shown, ts, ua?, endpoint?}
    """
    from urllib.parse import urlparse

    from models import PushSubscription

    try:
        body = await request.json()
    except Exception:
        body = {}
    nid = body.get("nid", "")
    shown = body.get("shown")
    ts = body.get("ts")
    ua = (body.get("ua") or "")[:120]
    endpoint = body.get("endpoint", "") or ""

    sub_id = ""
    host = ""
    if endpoint:
        host = urlparse(endpoint).netloc
        sub = db.query(PushSubscription).filter(
            PushSubscription.endpoint == endpoint
        ).first()
        if sub:
            sub_id = sub.id
            sub.last_ack_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
    logger.info(
        "push ack: nid=%s sub=%s host=%s shown=%s ts=%s ua=%s",
        nid, sub_id, host, shown, ts, ua,
    )
    return {"status": "ok"}
