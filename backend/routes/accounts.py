from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel

from backend.db import get_session
from backend.models import Account

router = APIRouter(prefix="/accounts", tags=["accounts"])

class AccountToggleRequest(BaseModel):
    is_active: bool

@router.get("")
async def list_accounts(session: Session = Depends(get_session)):
    accounts = session.exec(select(Account)).all()
    return [{"id": a.id, "email": a.email, "is_active": a.is_active} for a in accounts]

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
