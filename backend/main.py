import base64
import logging
import secrets
import os
import json
import httpx
import urllib.parse
from contextlib import asynccontextmanager
from typing import Optional, List
from datetime import datetime, timedelta
from email.utils import getaddresses

from fastapi import FastAPI, Depends, Request, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv
from email.mime.text import MIMEText
from pydantic import BaseModel

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlmodel import Session, select, delete
from sqlalchemy import desc
from diskcache import Cache

from backend.db import create_db_and_tables, get_session, engine
from backend.models import Account, Setting, RecentContact, GoogleContact

# Initialize Limiter
limiter = Limiter(key_func=get_remote_address)

# Initialize Cache
cache = Cache(".cache_dir")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env file
# Try loading from the root or from the backend directory
load_dotenv() 
if not os.getenv("GOOGLE_CLIENT_ID"):
    # Try relative to this file
    dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)

# Define Scopes
def get_requested_scopes():
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
        "https://www.googleapis.com/auth/contacts.readonly",
        "https://www.googleapis.com/auth/contacts.other.readonly",
        "https://www.googleapis.com/auth/userinfo.profile"
    ]
    # Default-deny the app's use of or requests for the messages.delete or messages.batchDelete scope
    # unless .env exists and contains a ENABLE_DELETION_SCOPE=true.
    if os.getenv("ENABLE_DELETION_SCOPE") == "true":
        scopes.append("https://mail.google.com/")
    else:
        scopes.append("https://www.googleapis.com/auth/gmail.modify")
    return scopes

SCOPES = get_requested_scopes()

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
    app_type = os.getenv("OAUTH_APP_TYPE", "web")
    
    # Using local engine from backend.db
    from backend.db import engine
    with Session(engine) as session:
        client_id_setting = session.exec(select(Setting).where(Setting.key == "GOOGLE_CLIENT_ID")).first()
        client_secret_setting = session.exec(select(Setting).where(Setting.key == "GOOGLE_CLIENT_SECRET")).first()
        
        client_id = client_id or (client_id_setting.value if client_id_setting else None)
        client_secret = client_secret or (client_secret_setting.value if client_secret_setting else None)

    if not client_id:
        raise HTTPException(status_code=500, detail="Google Client ID not configured")
    if app_type == "web" and not client_secret:
        raise HTTPException(status_code=500, detail="Google Client Secret not configured for Web App mode")

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "app_type": app_type
        }
    }

@app.get("/settings")
async def get_settings(session: Session = Depends(get_session)):
    settings_list = session.exec(select(Setting)).all()
    settings_dict = {s.key: s.value for s in settings_list}
    
    # Check if set via environment variables
    env_client_id = os.getenv("GOOGLE_CLIENT_ID")
    env_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    app_type = os.getenv("OAUTH_APP_TYPE", "web")
    
    return {
        "GOOGLE_CLIENT_ID": env_client_id or settings_dict.get("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_CLIENT_SECRET": env_client_secret or settings_dict.get("GOOGLE_CLIENT_SECRET", ""),
        "OAUTH_APP_TYPE": app_type,
        "is_client_id_env": env_client_id is not None,
        "is_client_secret_env": env_client_secret is not None,
        # Default appearance settings
        "THEME": settings_dict.get("THEME", "automatic"),
        "SHOW_DISCLOSURE_IF_SINGLE": settings_dict.get("SHOW_DISCLOSURE_IF_SINGLE", "false"),
        "SHOW_STARRED": settings_dict.get("SHOW_STARRED", "false"),
        "ALWAYS_COLLAPSE_SIDEBAR": settings_dict.get("ALWAYS_COLLAPSE_SIDEBAR", "false"),
        "COMPOSE_NEW_WINDOW": settings_dict.get("COMPOSE_NEW_WINDOW", "true"),
        "WARN_BEFORE_DELETE": settings_dict.get("WARN_BEFORE_DELETE", "true"),
        "MARK_READ_AUTOMATICALLY": settings_dict.get("MARK_READ_AUTOMATICALLY", "true"),
        "AUTOCOMPLETE_RECENTS": settings_dict.get("AUTOCOMPLETE_RECENTS", "true"),
        "AUTOCOMPLETE_ENABLED_ACCOUNTS": settings_dict.get("AUTOCOMPLETE_ENABLED_ACCOUNTS", ""),
        "CAN_PERMANENTLY_DELETE": "https://mail.google.com/" in SCOPES
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

@app.get("/stats")
async def get_stats(session: Session = Depends(get_session)):
    try:
        # Accounts
        account_count = session.exec(select(Account)).all()
        
        # Contacts
        recent_count = session.exec(select(RecentContact)).all()
        google_contact_count = session.exec(select(GoogleContact)).all()
        
        # Database size
        db_size = 0
        if os.path.exists("prosciutto.db"):
            db_size = os.path.getsize("prosciutto.db")
            
        # Cache stats
        cache_dir_size = 0
        if os.path.exists(".cache_dir"):
            for root, dirs, files in os.walk(".cache_dir"):
                for f in files:
                    cache_dir_size += os.path.getsize(os.path.join(root, f))

        return {
            "accounts": len(account_count),
            "recent_contacts": len(recent_count),
            "google_contacts": len(google_contact_count),
            "db_size_bytes": db_size,
            "cache_size_bytes": cache_dir_size,
            "deletion_scope_enabled": "https://mail.google.com/" in SCOPES
        }
    except Exception as e:
        logger.error(f"Error gathering stats: {e}")
        return {"error": str(e)}

@app.post("/contacts/clear")
async def clear_contacts(session: Session = Depends(get_session)):
    try:
        session.exec(delete(RecentContact))
        session.exec(delete(GoogleContact))
        # Also clear sync tokens so they re-sync from scratch next time
        accounts = session.exec(select(Account)).all()
        for acc in accounts:
            acc.sync_token = None
            acc.other_sync_token = None
            acc.last_contact_sync = None
            session.add(acc)
        session.commit()
        return {"message": "Local contacts and recents cleared"}
    except Exception as e:
        logger.error(f"Error clearing contacts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/accounts/{account_id}")
async def delete_account(account_id: int, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    session.delete(account)
    session.commit()
    return {"message": "Account deleted"}

class AccountToggleRequest(BaseModel):
    is_active: bool

@app.patch("/accounts/{account_id}/toggle-active")
async def toggle_account_active(account_id: int, request: AccountToggleRequest, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    account.is_active = request.is_active
    session.add(account)
    session.commit()
    return {"message": "Account status updated", "is_active": account.is_active}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
@limiter.limit("5/minute")
async def root(request: Request):
    return FileResponse(os.path.join(BASE_DIR, "../frontend/index.html"))

app.mount("/styles", StaticFiles(directory=os.path.join(BASE_DIR, "../frontend/styles")), name="styles")

import hashlib

def generate_pkce_verifier():
    return secrets.token_urlsafe(64)

def generate_pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).decode('utf-8').replace('=', '')

@app.get("/auth/login")
async def login(request: Request, session: Session = Depends(get_session)):
    client_config = get_client_config()
    app_type = client_config["web"].get("app_type", "web")
    redirect_uri = str(request.url_for("auth_callback"))
    # In some environments (like behind a proxy), url_for might return http instead of https
    if os.getenv("FORCE_HTTPS"):
        redirect_uri = redirect_uri.replace("http://", "https://")
    
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

    if app_type == "desktop":
        verifier = generate_pkce_verifier()
        challenge = generate_pkce_challenge(verifier)
        # Store verifier in DB for callback
        # Use a setting to store it temporarily
        setting = session.exec(select(Setting).where(Setting.key == "LAST_OAUTH_VERIFIER")).first()
        if not setting:
            setting = Setting(key="LAST_OAUTH_VERIFIER", value=verifier)
        else:
            setting.value = verifier
        session.add(setting)
        session.commit()
        
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    
    auth_url = f"{client_config['web']['auth_uri']}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    client_config = get_client_config()
    app_type = client_config["web"].get("app_type", "web")
    redirect_uri = str(request.url_for("auth_callback"))
    if os.getenv("FORCE_HTTPS"):
        redirect_uri = redirect_uri.replace("http://", "https://")
    
    token_url = client_config["web"]["token_uri"]
    data = {
        "code": code,
        "client_id": client_config["web"]["client_id"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    if app_type == "web":
        data["client_secret"] = client_config["web"]["client_secret"]
    else:
        # Desktop mode using PKCE
        verifier_setting = session.exec(select(Setting).where(Setting.key == "LAST_OAUTH_VERIFIER")).first()
        if not verifier_setting:
            raise HTTPException(status_code=400, detail="Missing code verifier for PKCE exchange")
        data["code_verifier"] = verifier_setting.value
    
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
        client_secret=client_config["web"].get("client_secret") if app_type == "web" else None,
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
    
    # Trigger warm-up sync for recent contacts and google contacts
    background_tasks.add_task(sync_recent_contacts_warmup, account.id)
    background_tasks.add_task(sync_google_contacts, account.id)
    
    # Redirect to frontend
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8000")
    return RedirectResponse(frontend_url)

@app.get("/accounts")
async def list_accounts(session: Session = Depends(get_session)):
    accounts = session.exec(select(Account)).all()
    return [{"id": a.id, "email": a.email, "is_active": a.is_active} for a in accounts]

def get_google_credentials(account_id: int, session: Session):
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

        return creds
    except Exception as e:
        logger.error(f"Failed to get Google credentials for account {account_id}: {str(e)}")
        return None

def get_gmail_service(account_id: int, session: Session):
    creds = get_google_credentials(account_id, session)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)

def get_people_service(account_id: int, session: Session):
    creds = get_google_credentials(account_id, session)
    if not creds:
        return None
    return build("people", "v1", credentials=creds)

def extract_contacts(address_str: str) -> List[dict]:
    if not address_str:
        return []
    contacts = []
    # getaddresses takes a list of strings
    for name, email_addr in getaddresses([address_str]):
        if email_addr:
            contacts.append({"name": name, "email": email_addr})
    return contacts

async def update_recent_contact(account_id: int, email: str, name: Optional[str], session: Session):
    # Check if exists
    existing = session.exec(
        select(RecentContact)
        .where(RecentContact.account_id == account_id)
        .where(RecentContact.email == email)
    ).first()
    
    if existing:
        existing.last_interacted = datetime.utcnow()
        if name and not existing.name:
            existing.name = name
        session.add(existing)
    else:
        new_recent = RecentContact(
            account_id=account_id,
            email=email,
            name=name,
            last_interacted=datetime.utcnow()
        )
        session.add(new_recent)
    
    session.commit()
    
    # Purge entries older than 90 days
    ninety_days_ago = datetime.utcnow() - timedelta(days=90)
    session.exec(
        delete(RecentContact)
        .where(RecentContact.account_id == account_id)
        .where(RecentContact.last_interacted < ninety_days_ago)
    )
    session.commit()
    
    # Enforce limit of 100 unique recipients per account
    recents = session.exec(
        select(RecentContact)
        .where(RecentContact.account_id == account_id)
        .order_by(desc(RecentContact.last_interacted))
    ).all()
    
    if len(recents) > 100:
        for extra in recents[100:]:
            session.delete(extra)
        session.commit()

async def sync_google_contacts(account_id: int):
    # Sync both connections and other contacts
    with Session(engine) as session:
        account = session.get(Account, account_id)
        if not account: return
        
        service = get_people_service(account_id, session)
        if not service: return

        # 1. Sync Connections
        try:
            sync_token = account.sync_token
            next_page_token = None
            while True:
                kwargs = {
                    "resourceName": "people/me",
                    "personFields": "names,emailAddresses,metadata,photos,memberships",
                    "pageSize": 1000,
                    "requestSyncToken": True
                }
                if sync_token:
                    kwargs["syncToken"] = sync_token
                if next_page_token:
                    kwargs["pageToken"] = next_page_token
                
                results = service.people().connections().list(**kwargs).execute()
                connections = results.get("connections", [])
                
                for person in connections:
                    res_name = person.get("resourceName")
                    metadata = person.get("metadata", {})
                    if metadata.get("deleted"):
                        existing = session.exec(
                            select(GoogleContact)
                            .where(GoogleContact.account_id == account_id)
                            .where(GoogleContact.resource_name == res_name)
                        ).all()
                        for c in existing: session.delete(c)
                    else:
                        name = person.get("names", [{}])[0].get("displayName") if person.get("names") else None
                        photo_url = person.get("photos", [{}])[0].get("url") if person.get("photos") else None
                        is_starred = False
                        if person.get("memberships"):
                            for m in person["memberships"]:
                                if m.get("contactGroupMembership", {}).get("contactGroupResourceName") == "contactGroups/starred":
                                    is_starred = True
                                    break
                        
                        emails = person.get("emailAddresses", [])
                        if not emails: continue
                        
                        # Delete old entries for this resource
                        existing = session.exec(
                            select(GoogleContact)
                            .where(GoogleContact.account_id == account_id)
                            .where(GoogleContact.resource_name == res_name)
                        ).all()
                        for c in existing: session.delete(c)
                        
                        for e in emails:
                            email_addr = e.get("value")
                            if email_addr:
                                new_contact = GoogleContact(
                                    account_id=account_id,
                                    resource_name=res_name,
                                    email=email_addr,
                                    name=name,
                                    photo_url=photo_url,
                                    is_starred=is_starred
                                )
                                session.add(new_contact)
                
                next_sync_token = results.get("nextSyncToken")
                next_page_token = results.get("nextPageToken")
                
                if not next_page_token:
                    account.sync_token = next_sync_token
                    session.add(account)
                    session.commit()
                    break
        except Exception as e:
            logger.error(f"Error syncing connections for {account_id}: {e}")
            if "expired" in str(e).lower():
                account.sync_token = None
                session.add(account)
                session.commit()

        # 2. Sync Other Contacts
        try:
            other_sync_token = account.other_sync_token
            next_page_token = None
            while True:
                kwargs = {
                    "pageSize": 1000,
                    "readMask": "names,emailAddresses,metadata,photos",
                    "requestSyncToken": True
                }
                if other_sync_token:
                    kwargs["syncToken"] = other_sync_token
                if next_page_token:
                    kwargs["pageToken"] = next_page_token
                
                results = service.otherContacts().list(**kwargs).execute()
                other_contacts = results.get("otherContacts", [])
                
                for person in other_contacts:
                    res_name = person.get("resourceName")
                    metadata = person.get("metadata", {})
                    if metadata.get("deleted"):
                        existing = session.exec(
                            select(GoogleContact)
                            .where(GoogleContact.account_id == account_id)
                            .where(GoogleContact.resource_name == res_name)
                        ).all()
                        for c in existing: session.delete(c)
                    else:
                        name = person.get("names", [{}])[0].get("displayName") if person.get("names") else None
                        photo_url = person.get("photos", [{}])[0].get("url") if person.get("photos") else None
                        
                        emails = person.get("emailAddresses", [])
                        if not emails: continue
                        
                        # Delete old entries for this resource
                        existing = session.exec(
                            select(GoogleContact)
                            .where(GoogleContact.account_id == account_id)
                            .where(GoogleContact.resource_name == res_name)
                        ).all()
                        for c in existing: session.delete(c)
                        
                        for e in emails:
                            email_addr = e.get("value")
                            if email_addr:
                                new_contact = GoogleContact(
                                    account_id=account_id,
                                    resource_name=res_name,
                                    email=email_addr,
                                    name=name,
                                    photo_url=photo_url,
                                    is_starred=False
                                )
                                session.add(new_contact)
                
                next_sync_token = results.get("nextSyncToken")
                next_page_token = results.get("nextPageToken")
                
                if not next_page_token:
                    account.other_sync_token = next_sync_token
                    account.last_contact_sync = datetime.utcnow()
                    session.add(account)
                    session.commit()
                    break
        except Exception as e:
            logger.error(f"Error syncing other contacts for {account_id}: {e}")
            if "expired" in str(e).lower():
                account.other_sync_token = None
                session.add(account)
                session.commit()
async def sync_recent_contacts_warmup(account_id: int):
    # Create a new session since the one from Depends(get_session) will be closed
    with Session(engine) as session:
        service = get_gmail_service(account_id, session)
        if not service:
            return
        
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).strftime("%Y/%m/%d")
        query = f"in:sent after:{thirty_days_ago}"
        
        try:
            results = service.users().messages().list(userId="me", q=query, maxResults=500).execute()
            messages_meta = results.get("messages", [])
            
            if not messages_meta:
                return

            detailed_messages = get_detailed_messages_batch(
                service, 
                messages_meta, 
                format="metadata", 
                metadata_headers=["To", "Cc", "Bcc"]
            )
            
            for msg in detailed_messages:
                # internalDate is in ms
                msg_date = datetime.fromtimestamp(msg["internalDate"] / 1000.0)
                
                for header in ["to", "cc", "bcc"]:
                    val = msg.get(header)
                    if val:
                        for contact in extract_contacts(val):
                            existing = session.exec(
                                select(RecentContact)
                                .where(RecentContact.account_id == account_id)
                                .where(RecentContact.email == contact["email"])
                            ).first()
                            
                            if existing:
                                if msg_date > existing.last_interacted:
                                    existing.last_interacted = msg_date
                                if contact["name"] and not existing.name:
                                    existing.name = contact["name"]
                                session.add(existing)
                            else:
                                new_recent = RecentContact(
                                    account_id=account_id,
                                    email=contact["email"],
                                    name=contact["name"],
                                    last_interacted=msg_date
                                )
                                session.add(new_recent)
            
            session.commit()
            
            # Purge entries older than 90 days
            ninety_days_ago = datetime.utcnow() - timedelta(days=90)
            session.exec(
                delete(RecentContact)
                .where(RecentContact.account_id == account_id)
                .where(RecentContact.last_interacted < ninety_days_ago)
            )
            session.commit()
            
            # Final cleanup to keep only top 100
            recents = session.exec(
                select(RecentContact)
                .where(RecentContact.account_id == account_id)
                .order_by(desc(RecentContact.last_interacted))
            ).all()
            
            if len(recents) > 100:
                for extra in recents[100:]:
                    session.delete(extra)
                session.commit()
                
        except Exception as e:
            logger.error(f"Error during recent contacts warm-up for account {account_id}: {e}")

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
        msg_data = {
            "id": response["id"],
            "snippet": response.get("snippet", ""),
            "threadId": response.get("threadId", ""),
            "labelIds": response.get("labelIds") or [],
            "internalDate": int(response.get("internalDate", 0)),
        }
        
        # Include all requested metadata headers
        if metadata_headers:
            for header_name in metadata_headers:
                msg_data[header_name] = get_header(headers, header_name)
                # Keep lowercase version for backwards compatibility/internal logic if needed
                msg_data[header_name.lower()] = msg_data[header_name]
        else:
            # Default headers if none specified
            msg_data["subject"] = get_header(headers, "Subject")
            msg_data["from"] = get_header(headers, "From")
            msg_data["date"] = get_header(headers, "Date")
            # Also provide title-case for frontend consistency if default
            msg_data["Subject"] = msg_data["subject"]
            msg_data["From"] = msg_data["from"]
            msg_data["Date"] = msg_data["date"]
            
        detailed_messages_dict[request_id] = msg_data
        logger.debug(f"Message {response['id']} labels: {detailed_messages_dict[request_id]['labelIds']}")

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

class CreateLabelRequest(BaseModel):
    name: str

@app.post("/accounts/{account_id}/labels")
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
        logger.error(f"Error creating label: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/autocomplete")
async def autocomplete(q: str, account_ids: str = None, include_recents: bool = True, session: Session = Depends(get_session)):
    if not q or len(q) < 1:
        return []
    
    ids = []
    if account_ids:
        try:
            ids = [int(i) for i in account_ids.split(",") if i.strip()]
        except:
            pass
    
    # If no ids provided, search across all active accounts? 
    # Or should it be empty if none provided?
    # Spec says "The frontend should merge results from all 'checked' accounts in the settings".
    # So if none are checked, ids will be empty.
    
    results = []
    
    # 1. Search Recents (Priority 1)
    if include_recents:
        # Search recents across all active accounts or only checked ones?
        # Typically recents should be for all accounts the user is using.
        # But let's limit it to checked accounts if ids is provided, 
        # or all active if ids is empty (backwards compat).
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
    
    if not ids:
        # If no account contacts are enabled, we might still have recents.
        # If we reached here and ids is empty, and include_recents was false,
        # or if we just want to return what we have (recents).
        pass
    else:
        # 2. Search Google Contacts Starred (Priority 2)
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
            
        # 3. Search Google Contacts General (Priority 3)
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

    # Deduplicate and rank
    unique_results = {}
    for r in results:
        email = r["email"].lower()
        if email not in unique_results or r["priority"] < unique_results[email]["priority"]:
            unique_results[email] = r
            
    sorted_results = sorted(unique_results.values(), key=lambda x: (x["priority"], x["name"] or x["email"]))
    
    return sorted_results[:20]

@app.get("/accounts/{account_id}/sync-contacts")
async def trigger_contact_sync(account_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    background_tasks.add_task(sync_google_contacts, account_id)
    return {"message": "Contact sync started in background"}

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
            "labelIds": label_ids,
            "internalDate": int(msg.get("internalDate", 0))
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

class BatchModifyRequest(BaseModel):
    ids: list[str]
    addLabelIds: list[str] = []
    removeLabelIds: list[str] = []

class BatchDeleteRequest(BaseModel):
    ids: list[str]

@app.post("/accounts/{account_id}/messages/batch-delete")
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

@app.delete("/accounts/{account_id}/messages/{message_id}")
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

        # Use 'html' subtype if isHtml is true, otherwise default to 'plain'
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
        
        # Update recent contacts
        for addr in [request.to, request.cc, request.bcc]:
            if addr:
                for contact in extract_contacts(addr):
                    await update_recent_contact(account_id, contact["email"], contact["name"], session)
        
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
async def unified_messages(label: str = None, page_token: str = None, refresh: bool = False, session: Session = Depends(get_session)):
    cache_key = f"unified_messages_{label}_{page_token}"
    active_ids = {a.id for a in session.exec(select(Account).where(Account.is_active)).all()}
    
    if not refresh:
        cached_result = cache.get(cache_key)
        if cached_result:
            filtered_messages = [m for m in cached_result["messages"] if m["accountId"] in active_ids]
            return {"messages": filtered_messages, "nextPageToken": cached_result["nextPageToken"]}

    accounts = session.exec(select(Account)).all()
    
    all_messages = []
    
    # Decode page_token if provided (it's a base64-encoded JSON mapping account_id -> individual token)
    tokens = {}
    if page_token:
        try:
            tokens = json.loads(base64.urlsafe_b64decode(page_token).decode("utf-8"))
        except Exception:
            tokens = {}
            
    new_tokens = {}
    
    # We could use asyncio to fetch in parallel
    for account in accounts:
        try:
            acc_id_str = str(account.id)
            # If we have a page_token but this account has no more pages, skip it
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
            
            # Capture the next token for this account if it exists
            if results.get("nextPageToken"):
                new_tokens[acc_id_str] = results["nextPageToken"]
            
            detailed_messages = get_detailed_messages_batch(service, messages_meta, format="metadata", metadata_headers=["Subject", "From", "Date"])
            
            for m in detailed_messages:
                m["accountEmail"] = account.email
                m["accountId"] = account.id
                all_messages.append(m)
        except Exception:
            # For unified view, we might want to just skip failed accounts or log them
            continue
    
    # Encode new tokens into a single base64 string for the frontend
    next_page_token_str = None
    if new_tokens:
        next_page_token_str = base64.urlsafe_b64encode(json.dumps(new_tokens).encode("utf-8")).decode("utf-8")
        
    # Sort by date descending
    all_messages.sort(key=lambda x: x["internalDate"], reverse=True)
    response_data = {"messages": all_messages, "nextPageToken": next_page_token_str}
    cache.set(cache_key, response_data, expire=300)
    
    filtered_messages = [m for m in all_messages if m["accountId"] in active_ids]
    return {"messages": filtered_messages, "nextPageToken": next_page_token_str}

@app.get("/unified/search")
async def unified_search(
    q: str = None, 
    page_token: str = None, 
    max_results: int = 20, 
    session: Session = Depends(get_session)
):
    cache_key = f"unified_search_{q}_{page_token}_{max_results}"
    active_ids = {a.id for a in session.exec(select(Account).where(Account.is_active)).all()}
    
    cached_result = cache.get(cache_key)
    if cached_result:
        filtered_messages = [m for m in cached_result["messages"] if m["accountId"] in active_ids]
        return {"messages": filtered_messages, "nextPageToken": cached_result["nextPageToken"]}

    accounts = session.exec(select(Account)).all()
    all_messages = []
    
    # Decode page_token if provided (it's a base64-encoded JSON mapping account_id -> individual token)
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
    
    # Encode new tokens into a single base64 string for the frontend
    next_page_token_str = None
    if new_tokens:
        next_page_token_str = base64.urlsafe_b64encode(json.dumps(new_tokens).encode("utf-8")).decode("utf-8")
        
    # Sort by date descending
    all_messages.sort(key=lambda x: x["internalDate"], reverse=True)
    response_data = {"messages": all_messages, "nextPageToken": next_page_token_str}
    cache.set(cache_key, response_data, expire=300)
    
    filtered_messages = [m for m in all_messages if m["accountId"] in active_ids]
    return {"messages": filtered_messages, "nextPageToken": next_page_token_str}

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
