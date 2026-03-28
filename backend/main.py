from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
import os
import json
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlmodel import Session, select
from .db import create_db_and_tables, get_session
from .models import Account, Setting

# Initialize Limiter
limiter = Limiter(key_func=get_remote_address)

# Define Scopes
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid"
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup database or other resources if needed
    create_db_and_tables()
    yield
    # Cleanup

app = FastAPI(title="Gmail API Web App", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

def get_client_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    # Using local engine from db.py
    from .db import engine
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

@app.post("/settings")
async def update_settings(settings: dict, session: Session = Depends(get_session)):
    for key, value in settings.items():
        setting = session.exec(select(Setting).where(Setting.key == key)).first()
        if not setting:
            setting = Setting(key=key, value=value)
        else:
            setting.value = value
        session.add(setting)
    session.commit()
    return {"message": "Settings updated"}

@app.get("/")
@limiter.limit("5/minute")
async def root():
    return {"message": "Gmail API Web App is running"}

@app.get("/auth/login")
async def login(request: Request):
    client_config = get_client_config()
    redirect_uri = str(request.url_for("auth_callback"))
    # In some environments (like behind a proxy), url_for might return http instead of https
    if os.getenv("FORCE_HTTPS"):
        redirect_uri = redirect_uri.replace("http://", "https://")
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    # Store state in session or cookie if needed for security, 
    # but for this MVP let's keep it simple
    return RedirectResponse(authorization_url)

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str, session: Session = Depends(get_session)):
    client_config = get_client_config()
    redirect_uri = str(request.url_for("auth_callback"))
    if os.getenv("FORCE_HTTPS"):
        redirect_uri = redirect_uri.replace("http://", "https://")
    
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
