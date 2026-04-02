import os
import logging
from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from backend.db import get_session
from backend.models import Account, Setting, RecentContact, GoogleContact
from backend.core.config import get_requested_scopes

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

@router.get("/settings")
async def get_settings(session: Session = Depends(get_session)):
    settings_list = session.exec(select(Setting)).all()
    settings_dict = {s.key: s.value for s in settings_list}
    
    env_client_id = os.getenv("GOOGLE_CLIENT_ID")
    env_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    env_enable_deletion = os.getenv("ENABLE_DELETION_SCOPE")
    app_type = os.getenv("OAUTH_APP_TYPE", "web")
    
    actual_enable_deletion = env_enable_deletion if env_enable_deletion is not None else settings_dict.get("ENABLE_DELETION_SCOPE", "false")

    return {
        "GOOGLE_CLIENT_ID": env_client_id or settings_dict.get("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_CLIENT_SECRET": env_client_secret or settings_dict.get("GOOGLE_CLIENT_SECRET", ""),
        "OAUTH_APP_TYPE": app_type,
        "is_client_id_env": env_client_id is not None,
        "is_client_secret_env": env_client_secret is not None,
        "is_enable_deletion_env": env_enable_deletion is not None,
        "ENABLE_DELETION_SCOPE": actual_enable_deletion,
        "THEME": settings_dict.get("THEME", "automatic"),
        "SHOW_DISCLOSURE_IF_SINGLE": settings_dict.get("SHOW_DISCLOSURE_IF_SINGLE", "false"),
        "SHOW_STARRED": settings_dict.get("SHOW_STARRED", "false"),
        "ALWAYS_COLLAPSE_SIDEBAR": settings_dict.get("ALWAYS_COLLAPSE_SIDEBAR", "false"),
        "ALWAYS_OPEN_IN_SIDE_PANEL": settings_dict.get("ALWAYS_OPEN_IN_SIDE_PANEL", "true"),
        "DEFAULT_COMPOSE_ACCOUNT": settings_dict.get("DEFAULT_COMPOSE_ACCOUNT", ""),
        "COMPOSE_NEW_WINDOW": settings_dict.get("COMPOSE_NEW_WINDOW", "true"),
        "WARN_BEFORE_DELETE": settings_dict.get("WARN_BEFORE_DELETE", "true"),
        "MARK_READ_AUTOMATICALLY": settings_dict.get("MARK_READ_AUTOMATICALLY", "true"),
        "AUTOCOMPLETE_RECENTS": settings_dict.get("AUTOCOMPLETE_RECENTS", "true"),
        "AUTOCOMPLETE_ENABLED_ACCOUNTS": settings_dict.get("AUTOCOMPLETE_ENABLED_ACCOUNTS", ""),
        "CAN_PERMANENTLY_DELETE": "https://mail.google.com/" in get_requested_scopes()
    }

@router.post("/settings")
async def update_settings(settings: dict, session: Session = Depends(get_session)):
    for key, value in settings.items():
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

@router.get("/stats")
async def get_stats(session: Session = Depends(get_session)):
    try:
        account_count = session.exec(select(Account)).all()
        recent_count = session.exec(select(RecentContact)).all()
        google_contact_count = session.exec(select(GoogleContact)).all()
        
        db_size = 0
        if os.path.exists("prosciutto.db"):
            db_size = os.path.getsize("prosciutto.db")
            
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
            "deletion_scope_enabled": actual_enable_deletion == "true"
        }
    except Exception as e:
        logger.error(f"Error gathering stats: {e}")
        return {"error": str(e)}
