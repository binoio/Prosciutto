import json
import base64
from typing import Optional, List
from sqlmodel import Session, select
from diskcache import Cache

from backend.models import Account
from backend.services.gmail_service import get_gmail_service, get_detailed_messages_batch

cache = Cache(".cache_dir")

async def get_unified_messages(
    session: Session, 
    label: str = None, 
    page_token: str = None, 
    refresh: bool = False
):
    cache_key = f"unified_messages_{label}_{page_token}"
    active_ids = {a.id for a in session.exec(select(Account).where(Account.is_active)).all()}
    
    if not refresh:
        cached_result = cache.get(cache_key)
        if cached_result:
            filtered_messages = [m for m in cached_result["messages"] if m["accountId"] in active_ids]
            return {"messages": filtered_messages, "nextPageToken": cached_result["nextPageToken"]}

    accounts = session.exec(select(Account)).all()
    all_messages = []
    
    tokens = {}
    if page_token:
        try:
            tokens = json.loads(base64.urlsafe_b64decode(page_token).decode("utf-8"))
        except Exception:
            tokens = {}
            
    new_tokens = {}
    
    for account in accounts:
        try:
            acc_id_str = str(account.id)
            if page_token and acc_id_str not in tokens:
                continue
                
            service = get_gmail_service(account.id, session)
            if not service: continue
            
            kwargs = {"userId": "me", "maxResults": 50}
            if label:
                kwargs["labelIds"] = [label]
            if acc_id_str in tokens:
                kwargs["pageToken"] = tokens[acc_id_str]

            results = service.users().messages().list(**kwargs).execute()
            messages_meta = results.get("messages", [])
            
            if results.get("nextPageToken"):
                new_tokens[acc_id_str] = results["nextPageToken"]
            
            detailed_messages = get_detailed_messages_batch(service, messages_meta, format="metadata", metadata_headers=["Subject", "From", "Date"])
            
            for m in detailed_messages:
                m["accountEmail"] = account.email
                m["accountId"] = account.id
                all_messages.append(m)
        except Exception:
            continue
    
    next_page_token_str = None
    if new_tokens:
        next_page_token_str = base64.urlsafe_b64encode(json.dumps(new_tokens).encode("utf-8")).decode("utf-8")
        
    all_messages.sort(key=lambda x: x["internalDate"], reverse=True)
    response_data = {"messages": all_messages, "nextPageToken": next_page_token_str}
    cache.set(cache_key, response_data, expire=300)
    
    filtered_messages = [m for m in all_messages if m["accountId"] in active_ids]
    return {"messages": filtered_messages, "nextPageToken": next_page_token_str}

async def search_unified_messages(
    session: Session,
    q: str = None, 
    page_token: str = None, 
    max_results: int = 20
):
    cache_key = f"unified_search_{q}_{page_token}_{max_results}"
    active_ids = {a.id for a in session.exec(select(Account).where(Account.is_active)).all()}
    
    cached_result = cache.get(cache_key)
    if cached_result:
        filtered_messages = [m for m in cached_result["messages"] if m["accountId"] in active_ids]
        return {"messages": filtered_messages, "nextPageToken": cached_result["nextPageToken"]}

    accounts = session.exec(select(Account)).all()
    all_messages = []
    
    tokens = {}
    if page_token:
        try:
            tokens = json.loads(base64.urlsafe_b64decode(page_token).decode("utf-8"))
        except Exception:
            tokens = {}
            
    new_tokens = {}
    
    for account in accounts:
        try:
            acc_id_str = str(account.id)
            if page_token and acc_id_str not in tokens:
                continue
                
            service = get_gmail_service(account.id, session)
            if not service: continue
            
            kwargs = {
                "userId": "me", 
                "maxResults": max_results,
                "fields": "messages(id,threadId),nextPageToken"
            }
            if q:
                kwargs["q"] = q
            if acc_id_str in tokens:
                kwargs["pageToken"] = tokens[acc_id_str]

            results = service.users().messages().list(**kwargs).execute()
            messages_meta = results.get("messages", [])
            
            if results.get("nextPageToken"):
                new_tokens[acc_id_str] = results["nextPageToken"]
            
            detailed_messages = get_detailed_messages_batch(
                service, 
                messages_meta, 
                format="metadata", 
                metadata_headers=["Subject", "From", "Date"]
            )
            
            for m in detailed_messages:
                m["accountEmail"] = account.email
                m["accountId"] = account.id
                all_messages.append(m)
        except Exception:
            continue
    
    next_page_token_str = None
    if new_tokens:
        next_page_token_str = base64.urlsafe_b64encode(json.dumps(new_tokens).encode("utf-8")).decode("utf-8")
        
    all_messages.sort(key=lambda x: x["internalDate"], reverse=True)
    response_data = {"messages": all_messages, "nextPageToken": next_page_token_str}
    cache.set(cache_key, response_data, expire=300)
    
    filtered_messages = [m for m in all_messages if m["accountId"] in active_ids]
    return {"messages": filtered_messages, "nextPageToken": next_page_token_str}
