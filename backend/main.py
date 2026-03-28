import base64
import logging
from fastapi import FastAPI, Depends, Request, HTTPException
from typing import Optional
from fastapi.responses import RedirectResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
import os
import json
import httpx
import urllib.parse
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlmodel import Session, select
from backend.db import create_db_and_tables, get_session
from backend.models import Account, Setting
from diskcache import Cache

# Initialize Limiter
limiter = Limiter(key_func=get_remote_address)

# Initialize Cache
cache = Cache(".cache_dir")

# Define Scopes
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid"
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database or other resources if needed
    if not os.getenv("TESTING"):
        create_db_and_tables()
    yield
    # Cleanup

app = FastAPI(title="Prosciutto", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

def get_client_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    # Using local engine from backend.db
    from backend.db import engine
    with Session(engine) as session:
        client_id_setting = session.exec(select(Setting).where(Setting.key == "GOOGLE_CLIENT_ID")).first()
        client_secret_setting = session.exec(select(Setting).where(Setting.key == "GOOGLE_CLIENT_SECRET")).first()
        client_id = client_id or (client_id_setting.value if client_id_setting else None)
        client_secret = client_secret or (client_secret_setting.value if client_secret_setting else None)

    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Google Client ID or Secret not configured")

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

@app.get("/settings")
async def get_settings(session: Session = Depends(get_session)):
    settings_list = session.exec(select(Setting)).all()
    settings_dict = {s.key: s.value for s in settings_list}
    
    # Check if set via environment variables
    env_client_id = os.getenv("GOOGLE_CLIENT_ID")
    env_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    return {
        "GOOGLE_CLIENT_ID": env_client_id or settings_dict.get("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_CLIENT_SECRET": env_client_secret or settings_dict.get("GOOGLE_CLIENT_SECRET", ""),
        "is_client_id_env": env_client_id is not None,
        "is_client_secret_env": env_client_secret is not None,
        # Default appearance settings
        "THEME": settings_dict.get("THEME", "automatic"),
        "SHOW_DISCLOSURE_IF_SINGLE": settings_dict.get("SHOW_DISCLOSURE_IF_SINGLE", "false"),
        "SHOW_STARRED": settings_dict.get("SHOW_STARRED", "false"),
        "COMPOSE_NEW_WINDOW": settings_dict.get("COMPOSE_NEW_WINDOW", "true")
    }

@app.post("/settings")
async def update_settings(settings: dict, session: Session = Depends(get_session)):
    for key, value in settings.items():
        # Prevent updating env-locked settings
        if key in ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"] and os.getenv(key):
            continue
            
        setting = session.exec(select(Setting).where(Setting.key == key)).first()
        if not setting:
            setting = Setting(key=key, value=str(value))
        else:
            setting.value = str(value)
        session.add(setting)
    session.commit()
    return {"message": "Settings updated"}

@app.delete("/accounts/{account_id}")
async def delete_account(account_id: int, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    session.delete(account)
    session.commit()
    return {"message": "Account deleted"}

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
@limiter.limit("5/minute")
async def root(request: Request):
    return FileResponse(os.path.join(BASE_DIR, "../frontend/index.html"))

app.mount("/styles", StaticFiles(directory=os.path.join(BASE_DIR, "../frontend/styles")), name="styles")

import secrets

@app.get("/auth/login")
async def login(request: Request):
    client_config = get_client_config()
    redirect_uri = str(request.url_for("auth_callback"))
    # In some environments (like behind a proxy), url_for might return http instead of https
    if os.getenv("FORCE_HTTPS"):
        redirect_uri = redirect_uri.replace("http://", "https://")
    
    # Manually build authorization URL to avoid PKCE 'Missing code verifier' issues
    # we're not using a stateful session to store the code_verifier, and 
    # for 'Web Server' apps with a client_secret, PKCE is optional.
    params = {
        "client_id": client_config["web"]["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": secrets.token_hex(16)
    }
    
    auth_url = f"{client_config['web']['auth_uri']}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str, session: Session = Depends(get_session)):
    client_config = get_client_config()
    redirect_uri = str(request.url_for("auth_callback"))
    if os.getenv("FORCE_HTTPS"):
        redirect_uri = redirect_uri.replace("http://", "https://")
    
    # Manually exchange code for tokens to avoid PKCE 'Missing code verifier' errors
    # google-auth-oauthlib's Flow requires stateful session for PKCE which we don't have
    token_url = client_config["web"]["token_uri"]
    data = {
        "code": code,
        "client_id": client_config["web"]["client_id"],
        "client_secret": client_config["web"]["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post(token_url, data=data)
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_response.text}")
        
        token_data = token_response.json()
    
    # Build credentials object manually
    credentials = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_url,
        client_id=client_config["web"]["client_id"],
        client_secret=client_config["web"]["client_secret"],
        scopes=SCOPES
    )
    
    # Get user email
    service = build("oauth2", "v2", credentials=credentials)
    user_info = service.userinfo().get().execute()
    email = user_info.get("email")
    
    # Store in DB
    account = session.exec(select(Account).where(Account.email == email)).first()
    if not account:
        account = Account(email=email, credentials_json=credentials.to_json())
    else:
        account.credentials_json = credentials.to_json()
    
    session.add(account)
    session.commit()
    
    return {"message": f"Account {email} added successfully"}

@app.get("/accounts")
async def list_accounts(session: Session = Depends(get_session)):
    accounts = session.exec(select(Account)).all()
    return [{"id": a.id, "email": a.email, "is_active": a.is_active} for a in accounts]

def get_gmail_service(account_id: int, session: Session):
    account = session.get(Account, account_id)
    if not account:
        logger.error(f"Account {account_id} not found in database")
        return None
    
    try:
        creds_dict = json.loads(account.credentials_json)
        creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)
        
        # Check if creds need refresh
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request as GoogleRequest
            creds.refresh(GoogleRequest())
            # Update DB with new credentials
            account.credentials_json = creds.to_json()
            session.add(account)
            session.commit()
        
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        logger.error(f"Failed to build Gmail service for account {account_id}: {str(e)}")
        return None

def get_header(headers, name):
    for header in headers:
        if header["name"].lower() == name.lower():
            return header["value"]
    return None

def get_detailed_messages_batch(service, messages_meta, format="metadata", metadata_headers=None):
    if not messages_meta:
        return []

    detailed_messages_dict = {}

    def callback(request_id, response, exception):
        if exception is not None:
            # In a real app, we might want to log this
            return
        
        headers = response.get("payload", {}).get("headers", [])
        detailed_messages_dict[request_id] = {
            "id": response["id"],
            "snippet": response.get("snippet", ""),
            "threadId": response.get("threadId", ""),
            "internalDate": int(response.get("internalDate", 0)),
            "subject": get_header(headers, "Subject"),
            "from": get_header(headers, "From"),
            "date": get_header(headers, "Date")
        }

    batch = service.new_batch_http_request(callback=callback)
    
    for msg in messages_meta:
        kwargs = {"userId": "me", "id": msg["id"], "format": format}
        if metadata_headers:
            kwargs["metadataHeaders"] = metadata_headers
        batch.add(service.users().messages().get(**kwargs), request_id=msg["id"])
    
    batch.execute()

    # Re-order results according to original messages_meta order
    results = []
    for msg in messages_meta:
        if msg["id"] in detailed_messages_dict:
            results.append(detailed_messages_dict[msg["id"]])
    return results

@app.get("/accounts/{account_id}/labels")
async def list_labels(account_id: int, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        
        # Filter for user-defined labels or interesting ones
        user_labels = [
            {"id": l["id"], "name": l["name"], "type": l["type"]} 
            for l in labels 
            if l["type"] == "user"
        ]
        
        return user_labels
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/accounts/{account_id}/messages")
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

@app.get("/accounts/{account_id}/search")
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
        # Check for common Gmail API errors
        error_msg = str(e)
        if "rateLimitExceeded" in error_msg:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        elif "insufficientPermissions" in error_msg:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        elif "invalidArgument" in error_msg or "400" in error_msg:
            raise HTTPException(status_code=400, detail="Invalid query syntax")
        
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/accounts/{account_id}/messages/{message_id}")
async def get_message(account_id: int, message_id: str, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        msg = service.users().messages().get(userId="me", id=message_id).execute()
        
        # If the message is unread, mark it as read
        if "UNREAD" in msg.get("labelIds", []):
            try:
                service.users().messages().modify(
                    userId="me", 
                    id=message_id, 
                    body={"removeLabelIds": ["UNREAD"]}
                ).execute()
                # Clear cache so the unread state is updated in the list
                cache.clear()
            except Exception as e:
                logger.warning(f"Failed to mark message {message_id} as read: {str(e)}")

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
        
        # Comprehensive body extraction (text/plain and text/html)
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
            # Single part message
            mime_type = payload.get("mimeType")
            body_data = payload.get("body", {}).get("data", "")
            if body_data:
                content = base64.urlsafe_b64decode(body_data).decode()
                if mime_type == "text/plain":
                    text_body = content
                elif mime_type == "text/html":
                    html_body = content

        # Final fallback to snippet if nothing found
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
            "internalDate": int(msg.get("internalDate", 0))
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from email.mime.text import MIMEText
from pydantic import BaseModel

class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body: str
    threadId: Optional[str] = None
    inReplyTo: Optional[str] = None
    references: Optional[str] = None

class BatchModifyRequest(BaseModel):
    ids: list[str]
    addLabelIds: list[str] = []
    removeLabelIds: list[str] = []

@app.post("/accounts/{account_id}/messages/batch-modify")
async def batch_modify_messages(account_id: int, request: BatchModifyRequest, session: Session = Depends(get_session)):
    if not request.ids:
        return {"message": "No messages to update"}
        
    service = get_gmail_service(account_id, session)
    if not service:
        logger.error(f"Service building failed for account {account_id}")
        raise HTTPException(status_code=404, detail="Account not found or service building failed")
    
    try:
        # Clear all message-related caches since we're modifying state
        cache.clear()
        
        # If the action is specifically "Move to Trash", use the dedicated .trash() endpoint
        # The frontend sends addLabelIds: ["TRASH"] for this action.
        if "TRASH" in request.addLabelIds:
            logger.info(f"Trashing {len(request.ids)} messages for account {account_id}")
            for msg_id in request.ids:
                service.users().messages().trash(userId="me", id=msg_id).execute()
        else:
            # For other label-based actions, batchModify is more efficient
            logger.info(f"Batch modifying {len(request.ids)} messages for account {account_id}")
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
            logger.error(f"Insufficient permissions for account {account_id}: {str(e)}")
            raise HTTPException(
                status_code=403, 
                detail="Insufficient permissions. Please re-authenticate this account in Settings to grant required scopes."
            )
        logger.error(f"Gmail API error for account {account_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Error in batch_modify_messages for account {account_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/accounts/{account_id}/send")
async def send_email(account_id: int, request: SendEmailRequest, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        # Clear cache since message list will change
        cache.clear()

        message = MIMEText(request.body)
        message["to"] = request.to
        message["subject"] = request.subject
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
        
        return {"message": "Email sent", "result": send_result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/accounts/{account_id}/labels/{label_id}/empty")
async def empty_label(account_id: int, label_id: str, session: Session = Depends(get_session)):
    service = get_gmail_service(account_id, session)
    if not service:
        raise HTTPException(status_code=404, detail="Account not found")
    
    try:
        # Clear cache
        cache.clear()
        
        # Get all message IDs in that label
        # We can't use batchDelete for TRASH or SPAM if they're not trashed?
        # Actually, for emptying Trash/Spam, we want to PERMANENTLY delete them.
        
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
            # Don't loop forever in case of huge mailbox
            if len(messages) > 1000: break

        if not messages:
            return {"message": "Label is already empty"}
            
        ids = [m["id"] for m in messages]
        
        # Gmail API allows up to 1000 messages per batchDelete
        for i in range(0, len(ids), 1000):
            batch_ids = ids[i:i+1000]
            service.users().messages().batchDelete(
                userId="me",
                body={"ids": batch_ids}
            ).execute()
            
        return {"message": f"Emptied {len(ids)} messages from {label_id}"}
    except Exception as e:
        logger.error(f"Failed to empty {label_id} for account {account_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/unified/messages")
async def unified_messages(label: str = None, refresh: bool = False, session: Session = Depends(get_session)):
    cache_key = f"unified_messages_{label}"
    if not refresh:
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

    accounts = session.exec(select(Account).where(Account.is_active)).all()
    
    all_messages = []
    import asyncio
    
    # We could use asyncio to fetch in parallel
    for account in accounts:
        try:
            service = get_gmail_service(account.id, session)
            if not service: continue
            
            kwargs = {"userId": "me", "maxResults": 10}
            if label:
                kwargs["labelIds"] = [label]

            results = service.users().messages().list(**kwargs).execute()
            messages_meta = results.get("messages", [])
            
            detailed_messages = get_detailed_messages_batch(service, messages_meta, format="metadata", metadata_headers=["Subject", "From", "Date"])
            
            for m in detailed_messages:
                m["accountEmail"] = account.email
                m["accountId"] = account.id
                all_messages.append(m)
        except Exception:
            # For unified view, we might want to just skip failed accounts or log them
            continue
    
    # Sort by date descending
    all_messages.sort(key=lambda x: x["internalDate"], reverse=True)
    response_data = {"messages": all_messages, "nextPageToken": None}
    cache.set(cache_key, response_data, expire=300)
    return response_data

@app.delete("/unified/labels/{label_id}/empty")
async def empty_unified_label(label_id: str, session: Session = Depends(get_session)):
    accounts = session.exec(select(Account).where(Account.is_active)).all()
    results = []
    for account in accounts:
        try:
            res = await empty_label(account.id, label_id, session)
            results.append({account.email: res["message"]})
        except Exception as e:
            results.append({account.email: f"Error: {str(e)}"})
            
    # Clear cache
    cache.clear()
    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
