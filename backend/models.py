from typing import Optional, List
from sqlmodel import Field, SQLModel, Relationship
import json
from datetime import datetime

class Account(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True)
    # Storing credentials as JSON string for simplicity, or we could use another table
    # Google API OAuth2 credentials (access_token, refresh_token, etc.)
    credentials_json: str
    is_active: bool = Field(default=True)
    notifications_enabled: bool = Field(default=False)
    last_history_id: Optional[str] = None
    sync_token: Optional[str] = None
    other_sync_token: Optional[str] = None
    last_contact_sync: Optional[datetime] = None

class RecentContact(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id")
    email: str
    name: Optional[str] = None
    last_interacted: datetime = Field(default_factory=datetime.utcnow)

class GoogleContact(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="account.id")
    resource_name: str
    email: str
    name: Optional[str] = None
    photo_url: Optional[str] = None
    is_starred: bool = Field(default=False)

class Setting(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True)
    value: str

class PushSubscription(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    endpoint: str = Field(unique=True)
    p256dh: str
    auth: str
    user_agent: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class NewMailNotification(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: str
    account_id: int
    account_email: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    is_seen: bool = Field(default=False)
