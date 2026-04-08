from sqlmodel import SQLModel, create_engine, Session
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./prosciutto.db")
if os.getenv("TESTING"):
    DATABASE_URL = "sqlite:///./test.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def create_db_and_tables():
    from backend.models import Account, Setting, RecentContact, GoogleContact, PushSubscription, NewMailNotification # Ensure they are imported before calling create_all
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
