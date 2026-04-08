from typing import List, Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from pydantic import BaseModel

from backend.db import get_session
from backend.models import Account, PushSubscription, NewMailNotification
from backend.services.gmail_service import (
    get_gmail_service, 
    get_detailed_messages_batch, 
    check_new_messages_internal
)
from backend.core.config import VAPID_PUBLIC_KEY

router = APIRouter(prefix="/accounts", tags=["accounts"])

class AccountToggleRequest(BaseModel):
    is_active: bool

class PushSubscriptionRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str

@router.get("/push-config")
async def get_push_config():
    return {"public_key": VAPID_PUBLIC_KEY}

@router.post("/subscribe-push")
async def subscribe_push(request_data: PushSubscriptionRequest, request: Request, session: Session = Depends(get_session)):
    # Check if subscription already exists
    existing = session.exec(select(PushSubscription).where(PushSubscription.endpoint == request_data.endpoint)).first()
    if existing:
        existing.p256dh = request_data.p256dh
        existing.auth = request_data.auth
        existing.user_agent = request.headers.get("user-agent")
        session.add(existing)
    else:
        new_sub = PushSubscription(
            endpoint=request_data.endpoint,
            p256dh=request_data.p256dh,
            auth=request_data.auth,
            user_agent=request.headers.get("user-agent")
        )
        session.add(new_sub)
    
    session.commit()
    return {"status": "success"}

@router.post("/unsubscribe-push")
async def unsubscribe_push(request_data: PushSubscriptionRequest, session: Session = Depends(get_session)):
    existing = session.exec(select(PushSubscription).where(PushSubscription.endpoint == request_data.endpoint)).first()
    if existing:
        session.delete(existing)
        session.commit()
    return {"status": "success"}

@router.get("/check-new-messages")
async def check_new_messages(session: Session = Depends(get_session)):
    # Return notifications from the last 2 minutes to allow multiple tabs to see them
    two_minutes_ago = datetime.utcnow() - timedelta(minutes=2)
    notifications = session.exec(
        select(NewMailNotification).where(NewMailNotification.discovered_at > two_minutes_ago)
    ).all()
    
    # Background cleanup of older notifications
    five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
    old_notifications = session.exec(
        select(NewMailNotification).where(NewMailNotification.discovered_at < five_minutes_ago)
    ).all()
    for old in old_notifications:
        session.delete(old)
    session.commit()
    
    results = []
    for n in notifications:
        results.append({
            "id": n.message_id,
            "account_id": n.account_id,
            "account_email": n.account_email,
            "subject": n.subject,
            "from": n.sender
        })
    return results

@router.get("")
async def list_accounts(session: Session = Depends(get_session)):
    accounts = session.exec(select(Account)).all()
    return [{"id": a.id, "email": a.email, "is_active": a.is_active, "notifications_enabled": a.notifications_enabled} for a in accounts]

@router.delete("/{account_id}")
async def delete_account(account_id: int, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    session.delete(account)
    session.commit()
    return {"message": "Account deleted"}

@router.patch("/{account_id}/toggle-active")
async def toggle_account_active(account_id: int, request: AccountToggleRequest, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.is_active = request.is_active
    session.add(account)
    session.commit()
    return {"message": "Account status updated", "is_active": account.is_active}

@router.patch("/{account_id}/toggle-notifications")
async def toggle_account_notifications(account_id: int, request: AccountToggleRequest, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.notifications_enabled = request.is_active
    session.add(account)
    session.commit()
    return {"message": "Account notifications updated", "notifications_enabled": account.notifications_enabled}
