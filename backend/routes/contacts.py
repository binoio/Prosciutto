from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select, delete
from sqlalchemy import desc

from backend.db import get_session
from backend.models import Account, RecentContact, GoogleContact
from backend.services.people_service import sync_google_contacts

router = APIRouter(tags=["contacts"])

@router.post("/contacts/clear")
async def clear_contacts(session: Session = Depends(get_session)):
    try:
        session.exec(delete(RecentContact))
        session.exec(delete(GoogleContact))
        accounts = session.exec(select(Account)).all()
        for acc in accounts:
            acc.sync_token = None
            acc.other_sync_token = None
            acc.last_contact_sync = None
            session.add(acc)
        session.commit()
        return {"message": "Local contacts and recents cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/accounts/{account_id}/sync-contacts")
async def trigger_contact_sync(account_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    background_tasks.add_task(sync_google_contacts, account_id)
    return {"message": "Contact sync started in background"}

@router.get("/autocomplete")
async def autocomplete(q: str, account_ids: str = None, include_recents: bool = True, session: Session = Depends(get_session)):
    if not q or len(q) < 1:
        return []
    
    ids = []
    if account_ids:
        try:
            ids = [int(i) for i in account_ids.split(",") if i.strip()]
        except:
            pass
    
    results = []
    
    if include_recents:
        recent_ids = ids if ids else [a.id for a in session.exec(select(Account).where(Account.is_active == True)).all()]
        
        if recent_ids:
            recents = session.exec(
                select(RecentContact)
                .where(RecentContact.account_id.in_(recent_ids))
                .where(
                    (RecentContact.email.ilike(f"%{q}%")) | 
                    (RecentContact.name.ilike(f"%{q}%"))
                )
                .order_by(desc(RecentContact.last_interacted))
                .limit(50)
            ).all()
            
            for r in recents:
                results.append({
                    "email": r.email,
                    "name": r.name,
                    "type": "recent",
                    "priority": 1,
                    "account_id": r.account_id
                })
    
    if ids:
        starred = session.exec(
            select(GoogleContact)
            .where(GoogleContact.account_id.in_(ids))
            .where(GoogleContact.is_starred == True)
            .where(
                (GoogleContact.email.ilike(f"%{q}%")) | 
                (GoogleContact.name.ilike(f"%{q}%"))
            )
            .limit(50)
        ).all()
        
        for c in starred:
            results.append({
                "email": c.email,
                "name": c.name,
                "photo_url": c.photo_url,
                "type": "starred",
                "priority": 2,
                "account_id": c.account_id
            })
            
        others = session.exec(
            select(GoogleContact)
            .where(GoogleContact.account_id.in_(ids))
            .where(GoogleContact.is_starred == False)
            .where(
                (GoogleContact.email.ilike(f"%{q}%")) | 
                (GoogleContact.name.ilike(f"%{q}%"))
            )
            .limit(50)
        ).all()
        
        for c in others:
            results.append({
                "email": c.email,
                "name": c.name,
                "photo_url": c.photo_url,
                "type": "contact",
                "priority": 3,
                "account_id": c.account_id
            })

    unique_results = {}
    for r in results:
        email = r["email"].lower()
        if email not in unique_results or r["priority"] < unique_results[email]["priority"]:
            unique_results[email] = r
            
    sorted_results = sorted(unique_results.values(), key=lambda x: (x["priority"], x["name"] or x["email"]))
    
    return sorted_results[:20]
