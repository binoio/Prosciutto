from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel

from backend.db import get_session
from backend.models import Account
from backend.services.gmail_service import get_gmail_service, get_detailed_messages_batch

router = APIRouter(prefix="/accounts", tags=["accounts"])

class AccountToggleRequest(BaseModel):
    is_active: bool

@router.get("/check-new-messages")
async def check_new_messages(session: Session = Depends(get_session)):
    accounts = session.exec(select(Account).where(Account.is_active == True).where(Account.notifications_enabled == True)).all()
    new_messages_all = []

    for account in accounts:
        service = get_gmail_service(account.id, session)
        if not service:
            continue
        
        try:
            # Get the current profile to get the latest historyId
            profile = service.users().getProfile(userId='me').execute()
            current_history_id = profile.get('historyId')
            
            if not account.last_history_id:
                account.last_history_id = current_history_id
                session.add(account)
                session.commit()
                continue
            
            if current_history_id == account.last_history_id:
                continue
                
            # Fetch history since last_history_id
            history = service.users().history().list(userId='me', startHistoryId=account.last_history_id, historyTypes=['messageAdded']).execute()
            history_records = history.get('history', [])
            
            new_msg_metas = []
            for h in history_records:
                messages_added = h.get('messagesAdded', [])
                for ma in messages_added:
                    msg = ma.get('message')
                    if msg and 'INBOX' in msg.get('labelIds', []):
                        new_msg_metas.append(msg)
            
            if new_msg_metas:
                # Limit to 5 most recent
                new_msg_metas = new_msg_metas[-5:]
                detailed = get_detailed_messages_batch(service, new_msg_metas, format="metadata", metadata_headers=["Subject", "From"])
                for m in detailed:
                    m['account_email'] = account.email
                    m['account_id'] = account.id
                    new_messages_all.append(m)
            
            account.last_history_id = current_history_id
            session.add(account)
            session.commit()
            
        except Exception as e:
            print(f"Error checking new messages for {account.email}: {e}")
            # Fallback: if historyId is too old, just update it and move on
            try:
                profile = service.users().getProfile(userId='me').execute()
                account.last_history_id = profile.get('historyId')
                session.add(account)
                session.commit()
            except:
                pass

    return new_messages_all

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
