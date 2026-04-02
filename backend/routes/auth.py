import os
import secrets
import urllib.parse
import httpx
import json
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from backend.db import get_session
from backend.models import Account, Setting
from backend.core.config import get_client_config, get_requested_scopes
from backend.core.security import generate_pkce_verifier, generate_pkce_challenge
from backend.services.gmail_service import sync_recent_contacts_warmup
from backend.services.people_service import sync_google_contacts

router = APIRouter(prefix="/auth", tags=["auth"])

@router.get("/login")
async def login(request: Request, session: Session = Depends(get_session)):
    client_config = get_client_config()
    app_type = client_config["web"].get("app_type", "web")
    redirect_uri = str(request.url_for("auth_callback"))
    if os.getenv("FORCE_HTTPS"):
        redirect_uri = redirect_uri.replace("http://", "https://")
    
    params = {
        "client_id": client_config["web"]["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(get_requested_scopes()),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": secrets.token_hex(16)
    }

    if app_type == "desktop":
        verifier = generate_pkce_verifier()
        challenge = generate_pkce_challenge(verifier)
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

@router.get("/callback")
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
        verifier_setting = session.exec(select(Setting).where(Setting.key == "LAST_OAUTH_VERIFIER")).first()
        if not verifier_setting:
            raise HTTPException(status_code=400, detail="Missing code verifier for PKCE exchange")
        data["code_verifier"] = verifier_setting.value
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post(token_url, data=data)
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_response.text}")
        
        token_data = token_response.json()
    
    credentials = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_url,
        client_id=client_config["web"]["client_id"],
        client_secret=client_config["web"].get("client_secret") if app_type == "web" else None,
        scopes=get_requested_scopes()
    )
    
    service = build("oauth2", "v2", credentials=credentials)
    user_info = service.userinfo().get().execute()
    email = user_info.get("email")
    
    account = session.exec(select(Account).where(Account.email == email)).first()
    if not account:
        account = Account(email=email, credentials_json=credentials.to_json())
    else:
        account.credentials_json = credentials.to_json()
    
    session.add(account)
    session.commit()
    
    background_tasks.add_task(sync_recent_contacts_warmup, account.id)
    background_tasks.add_task(sync_google_contacts, account.id)
    
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8000")
    return RedirectResponse(frontend_url)
