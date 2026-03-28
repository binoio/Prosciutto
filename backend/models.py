from typing import Optional, List
from sqlmodel import Field, SQLModel, Relationship
import json

class Account(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    # Storing credentials as JSON string for simplicity, or we could use another table
    # Google API OAuth2 credentials (access_token, refresh_token, etc.)
    credentials_json: str
    is_active: bool = Field(default=True)

class Setting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: str
