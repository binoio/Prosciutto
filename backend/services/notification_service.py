import logging
import json
from typing import List, Optional
from pywebpush import webpush, WebPushException
from sqlmodel import Session, select

from backend.models import PushSubscription
from backend.core.config import VAPID_PRIVATE_KEY, VAPID_CLAIMS

logger = logging.getLogger(__name__)

def send_web_push(subscription: PushSubscription, data: dict):
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth": subscription.auth
                }
            },
            data=json.dumps(data),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
    except WebPushException as ex:
        logger.error(f"Web Push Error: {ex}")
        if ex.response and ex.response.status_code in [404, 410]:
            # Subscription has expired or is no longer valid
            return False
    except Exception as e:
        logger.error(f"Failed to send web push: {e}")
    return True

async def notify_all_subscriptions(data: dict, session: Session):
    subscriptions = session.exec(select(PushSubscription)).all()
    failed_subs = []
    for sub in subscriptions:
        success = send_web_push(sub, data)
        if not success:
            failed_subs.append(sub)
    
    if failed_subs:
        for sub in failed_subs:
            session.delete(sub)
        session.commit()
