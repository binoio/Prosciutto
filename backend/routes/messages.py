import logging
import base64
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from pydantic import BaseModel
from googleapiclient.errors import HttpError
from email.mime.text import MIMEText
from diskcache import Cache

from backend.db import get_session
from backend.services.gmail_service import (
    get_gmail_service, 
    get_detailed_messages_batch, 
    get_header, 
    update_recent_contact,
    extract_contacts
)
from backend.services.unified_service import (
    get_unified_messages, 
    search_unified_messages
)
from backend.models import Account

logger = logging.getLogger(__name__)
cache = Cache(".cache_dir")

router = APIRouter(tags=["messages"])

class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: Optional[str] = None
    bcc: Optional[str] = None
    isHtml: Optional[bool] = False
    threadId: Optional[str] = None
    inReplyTo: Optional[str] = None
    references: Optional[str] = None

class SaveDraftRequest(BaseModel):
    to: Optional[str] = ""
    subject: Optional[str] = ""
    body: Optional[str] = ""
    cc: Optional[str] = None
    bcc: Optional[str] = None
    isHtml: Optional[bool] = False
    threadId: Optional[str] = None
    inReplyTo: Optional[str] = None
    references: Optional[str] = None
    draftId: Optional[str] = None

class BatchModifyRequest(BaseModel):
    ids: List[str]
    addLabelIds: List[str] = []
    removeLabelIds: List[str] = []

class BatchDeleteRequest(BaseModel):
    ids: List[str]

class CreateLabelRequest(BaseModel):
    name: str

@router.get("/accounts/{account_id}/messages")
async def list_messages(account_id: int, label: str = None, page_token: str = None, refresh: bool = False, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    cache_key = f"messages_{account_id}_{label}_{page_token}"
    if not refresh:
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

    try:
        kwargs = {"userId": "me", "maxResults": 20}
        if label:
            kwargs["labelIds"] = [label]
        if page_token:
            kwargs["pageToken"] = page_token
        
        results = service.users().messages().list(**kwargs).execute()
        messages_meta = results.get("messages", [])
        next_page_token = results.get("nextPageToken")
        
        detailed_messages = get_detailed_messages_batch(service, messages_meta, format="metadata", metadata_headers=["Subject", "From", "Date"])
        
        response_data = {
            "messages": detailed_messages,
            "nextPageToken": next_page_token
        }
        cache.set(cache_key, response_data, expire=300)
        return response_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/accounts/{account_id}/search")
async def search_messages(
    account_id: int, 
    q: str = None, 
    page_token: str = None, 
    max_results: int = 20, 
    session: Session = Depends(get_session)
):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        kwargs = {
            "userId": "me", 
            "maxResults": max_results,
            "fields": "messages(id,threadId),nextPageToken"
        }
        if q:
            kwargs["q"] = q
        if page_token:
            kwargs["pageToken"] = page_token
        
        results = service.users().messages().list(**kwargs).execute()
        messages_meta = results.get("messages", [])
        next_page_token = results.get("nextPageToken")
        
        detailed_messages = get_detailed_messages_batch(
            service, 
            messages_meta, 
            format="metadata", 
            metadata_headers=["Subject", "From", "Date"]
        )
        
        return {
            "messages": detailed_messages,
            "nextPageToken": next_page_token
        }
    except Exception as e:
        error_msg = str(e)
        if "rateLimitExceeded" in error_msg:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        elif "insufficientPermissions" in error_msg:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        elif "invalidArgument" in error_msg or "400" in error_msg:
            raise HTTPException(status_code=400, detail="Invalid query syntax")
        
        raise HTTPException(status_code=500, detail=error_msg)

@router.get("/accounts/{account_id}/messages/{message_id}")
async def get_message(account_id: int, message_id: str, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        msg = service.users().messages().get(userId="me", id=message_id).execute()
        
        headers = msg.get("payload", {}).get("headers", [])
        subject = get_header(headers, "Subject")
        from_email = get_header(headers, "From")
        to_email = get_header(headers, "To")
        cc_email = get_header(headers, "Cc")
        date = get_header(headers, "Date")
        message_id_header = get_header(headers, "Message-ID")
        references_header = get_header(headers, "References")
        snippet = msg.get("snippet", "")
        thread_id = msg.get("threadId", "")
        label_ids = msg.get("labelIds", [])
        
        text_body = ""
        html_body = ""
        
        def extract_parts(parts):
            nonlocal text_body, html_body
            for part in parts:
                mime_type = part.get("mimeType")
                body_data = part.get("body", {}).get("data", "")
                
                if mime_type == "text/plain" and not text_body:
                    text_body = base64.urlsafe_b64decode(body_data).decode()
                elif mime_type == "text/html" and not html_body:
                    html_body = base64.urlsafe_b64decode(body_data).decode()
                elif mime_type.startswith("multipart/"):
                    extract_parts(part.get("parts", []))

        payload = msg.get("payload", {})
        if "parts" in payload:
            extract_parts(payload["parts"])
        else:
            mime_type = payload.get("mimeType")
            body_data = payload.get("body", {}).get("data", "")
            if body_data:
                content = base64.urlsafe_b64decode(body_data).decode()
                if mime_type == "text/plain":
                    text_body = content
                elif mime_type == "text/html":
                    html_body = content

        if not text_body and not html_body:
            text_body = snippet
            
        return {
            "id": message_id,
            "threadId": thread_id,
            "messageId": message_id_header,
            "references": references_header,
            "subject": subject,
            "from": from_email,
            "to": to_email,
            "cc": cc_email,
            "date": date,
            "body": text_body,
            "html_body": html_body,
            "snippet": snippet,
            "labelIds": label_ids,
            "internalDate": int(msg.get("internalDate", 0))
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/accounts/{account_id}/messages/batch-delete")
async def batch_delete_messages(account_id: int, request: BatchDeleteRequest, session: Session = Depends(get_session)):
    if not request.ids:
        return {"message": "No messages to delete"}
        
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        cache.clear()
        service.users().messages().batchDelete(userId="me", body={"ids": request.ids}).execute()
        return {"message": f"Successfully deleted {len(request.ids)} messages"}
    except Exception as e:
        logger.error(f"Error batch deleting messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/accounts/{account_id}/messages/{message_id}")
async def delete_message(account_id: int, message_id: str, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        cache.clear()
        service.users().messages().delete(userId="me", id=message_id).execute()
        return {"message": "Message permanently deleted"}
    except Exception as e:
        logger.error(f"Error deleting message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/accounts/{account_id}/messages/batch-modify")
async def batch_modify_messages(account_id: int, request: BatchModifyRequest, session: Session = Depends(get_session)):
    if not request.ids:
        return {"message": "No messages to update"}
        
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        cache.clear()
        if "TRASH" in request.addLabelIds:
            for msg_id in request.ids:
                service.users().messages().trash(userId="me", id=msg_id).execute()
        else:
            service.users().messages().batchModify(
                userId="me", 
                body={
                    "ids": request.ids,
                    "addLabelIds": request.addLabelIds,
                    "removeLabelIds": request.removeLabelIds
                }
            ).execute()
            
        return {"message": f"Updated {len(request.ids)} messages"}
    except HttpError as e:
        if e.resp.status == 403:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/accounts/{account_id}/drafts")
async def save_draft(account_id: int, request: SaveDraftRequest, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        cache.clear()
        message = MIMEText(request.body or "", 'html' if request.isHtml else 'plain')
        message["to"] = request.to or ""
        message["subject"] = request.subject or ""
        if request.cc:
            message["cc"] = request.cc
        if request.bcc:
            message["bcc"] = request.bcc
        if request.inReplyTo:
            message["In-Reply-To"] = request.inReplyTo
        if request.references:
            message["References"] = request.references
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        draft_body = {
            "message": {
                "raw": raw_message
            }
        }
        if request.threadId:
            draft_body["message"]["threadId"] = request.threadId
            
        if request.draftId:
            # Update existing draft
            result = service.users().drafts().update(
                userId="me",
                id=request.draftId,
                body=draft_body
            ).execute()
            msg = "Draft updated"
        else:
            # Create new draft
            result = service.users().drafts().create(
                userId="me",
                body=draft_body
            ).execute()
            msg = "Draft created"
        
        return {"message": msg, "draft": result}
    except Exception as e:
        logger.error(f"Error saving draft: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/accounts/{account_id}/send")
async def send_email(account_id: int, request: SendEmailRequest, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        cache.clear()
        message = MIMEText(request.body, 'html' if request.isHtml else 'plain')
        message["to"] = request.to
        message["subject"] = request.subject
        if request.cc:
            message["cc"] = request.cc
        if request.bcc:
            message["bcc"] = request.bcc
        if request.inReplyTo:
            message["In-Reply-To"] = request.inReplyTo
        if request.references:
            message["References"] = request.references
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        body = {"raw": raw_message}
        if request.threadId:
            body["threadId"] = request.threadId
            
        send_result = service.users().messages().send(
            userId="me",
            body=body
        ).execute()
        
        for addr in [request.to, request.cc, request.bcc]:
            if addr:
                for contact in extract_contacts(addr):
                    await update_recent_contact(account_id, contact["email"], contact["name"], session)
        
        return {"message": "Email sent", "result": send_result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/accounts/{account_id}/labels")
async def list_labels(account_id: int, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        user_labels = [
            {"id": l["id"], "name": l["name"], "type": l["type"]} 
            for l in labels 
            if l["type"] == "user"
        ]
        return user_labels
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/accounts/{account_id}/labels")
async def create_label(account_id: int, request: CreateLabelRequest, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        label = {
            "name": request.name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show"
        }
        created_label = service.users().labels().create(userId="me", body=label).execute()
        return created_label
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/accounts/{account_id}/labels/{label_id}/empty")
async def empty_label(account_id: int, label_id: str, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        cache.clear()
        messages = []
        next_page_token = None
        while True:
            kwargs = {"userId": "me", "labelIds": [label_id], "maxResults": 500}
            if next_page_token:
                kwargs["pageToken"] = next_page_token
            
            results = service.users().messages().list(**kwargs).execute()
            messages.extend(results.get("messages", []))
            next_page_token = results.get("nextPageToken")
            if not next_page_token:
                break
            if len(messages) > 1000: break

        if not messages:
            return {"message": "Label is already empty"}
            
        ids = [m["id"] for m in messages]
        for i in range(0, len(ids), 1000):
            batch_ids = ids[i:i+1000]
            service.users().messages().batchDelete(
                userId="me",
                body={"ids": batch_ids}
            ).execute()
            
        return {"message": f"Emptied {len(ids)} messages from {label_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/unified/messages")
async def unified_messages(label: str = None, page_token: str = None, refresh: bool = False, session: Session = Depends(get_session)):
    return await get_unified_messages(session, label, page_token, refresh)

@router.get("/unified/search")
async def unified_search(
    q: str = None, 
    page_token: str = None, 
    max_results: int = 20, 
    session: Session = Depends(get_session)
):
    return await search_unified_messages(session, q, page_token, max_results)

@router.delete("/unified/labels/{label_id}/empty")
async def empty_unified_label(label_id: str, session: Session = Depends(get_session)):
    from backend.models import Account
    from sqlmodel import select
    accounts = session.exec(select(Account).where(Account.is_active)).all()
    results = []
    for account in accounts:
        try:
            res = await empty_label(account.id, label_id, session)
            results.append({account.email: res["message"]})
        except Exception as e:
            results.append({account.email: f"Error: {str(e)}"})
            
    cache.clear()
    return {"results": results}
