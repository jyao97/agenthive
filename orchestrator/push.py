"""Web Push notification sender — best-effort, never blocks dispatch."""

import json
import logging

from config import VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_SUBJECT

logger = logging.getLogger("orchestrator.push")


def send_push_notification(title: str, body: str, url: str = "/") -> None:
    """Send a push notification to all subscribed browsers.

    Silently catches all errors — push is best-effort.
    Removes subscriptions that return 410 Gone (expired).
    """
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.debug("pywebpush not installed — skipping push")
        return

    from urllib.parse import urlparse

    from database import SessionLocal
    from models import PushSubscription

    db = SessionLocal()
    try:
        subs = db.query(PushSubscription).all()
        if not subs:
            return

        payload = json.dumps({"title": title, "body": body, "url": url})
        expired_ids = []

        for sub in subs:
            subscription_info = {
                "endpoint": sub.endpoint,
                "keys": {
                    "p256dh": sub.p256dh_key,
                    "auth": sub.auth_key,
                },
            }
            # aud must be the origin of the push endpoint (required by FCM)
            parsed = urlparse(sub.endpoint)
            aud = f"{parsed.scheme}://{parsed.netloc}"
            vapid_claims = {"sub": VAPID_SUBJECT, "aud": aud}
            try:
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=vapid_claims,
                )
            except WebPushException as e:
                if hasattr(e, "response") and e.response is not None:
                    if e.response.status_code == 410:
                        expired_ids.append(sub.id)
                        continue
                logger.warning("Push failed for %s: %s", sub.endpoint[:60], e)
            except Exception:
                logger.warning("Push error for %s", sub.endpoint[:60], exc_info=True)

        if expired_ids:
            db.query(PushSubscription).filter(
                PushSubscription.id.in_(expired_ids)
            ).delete(synchronize_session=False)
            db.commit()
            logger.info("Removed %d expired push subscriptions", len(expired_ids))
    except Exception:
        logger.debug("Push notification batch failed", exc_info=True)
    finally:
        db.close()
