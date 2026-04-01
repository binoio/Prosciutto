import os
import logging
from dotenv import load_dotenv
from sqlmodel import Session, select
from fastapi import HTTPException
from backend.models import Setting
from backend.db import engine

logger = logging.getLogger(__name__)

# Load .env file
load_dotenv()
if not os.getenv("GOOGLE_CLIENT_ID"):
    # Try relative to this file
    dotenv_path = os.path.join(os.path.dirname(__file__), "../../.env")
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)

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

def get_client_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    app_type = os.getenv("OAUTH_APP_TYPE", "web")
    
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
