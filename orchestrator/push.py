"""Push notification sender — best-effort, never blocks dispatch.

Supports two backends:
1. Web Push (VAPID) — browser-based, works when PWA is open
2. Telegram Bot — reliable push via Telegram Bot API
"""

import json
import logging

import requests as _requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    VAPID_PRIVATE_KEY,
    VAPID_PUBLIC_KEY,
    VAPID_SUBJECT,
)

logger = logging.getLogger("orchestrator.push")


def send_push_notification(title: str, body: str, url: str = "/") -> None:
    """Send a push notification via all configured backends."""
    _send_telegram(title, body, url)
    _send_webpush(title, body, url)


def _send_telegram(title: str, body: str, url: str = "/") -> None:
    """Send a notification via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        text = f"*{title}*\n{body}"
        _requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=5,
        )
    except _requests.exceptions.RequestException:
        logger.debug("Telegram send failed", exc_info=True)
    except Exception:
        logger.error("Telegram send unexpected error", exc_info=True)


def _send_webpush(title: str, body: str, url: str = "/") -> None:
    """Send a Web Push notification to all subscribed browsers."""
    if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
        return

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.debug("pywebpush not installed — skipping web push")
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
    except _requests.exceptions.RequestException:
        logger.debug("Push notification batch failed (network)", exc_info=True)
    except Exception:
        logger.error("Push notification batch failed", exc_info=True)
    finally:
        db.close()
