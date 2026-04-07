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
    
    enable_deletion = os.getenv("ENABLE_DELETION_SCOPE")
    if enable_deletion is None:
        try:
            with Session(engine) as session:
                setting = session.exec(select(Setting).where(Setting.key == "ENABLE_DELETION_SCOPE")).first()
                if setting:
                    enable_deletion = setting.value
        except Exception:
            pass

    if enable_deletion == "true":
        scopes.append("https://mail.google.com/")
    else:
        scopes.append("https://www.googleapis.com/auth/gmail.modify")
    return scopes

SCOPES = get_requested_scopes()

# VAPID Keys for Web Push
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "cCsAbx2v2CnxFRpXJvx6EpRo4YIjiBcoONTB5T3DFng")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "BGlyZ73Ur1gP4Pely9fKhgDB978KK0Xho50gaMDiukwUjMZNETxsJw4gUJ4X8tvPF-BaEiUoxFG8cbyVfnbHg6w")
VAPID_CLAIMS = {
    "sub": "mailto:admin@prosciutto.local"
}

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
