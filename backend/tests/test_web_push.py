import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from backend.main import app
from backend.db import get_session
from backend.models import PushSubscription
from backend.core.config import VAPID_PUBLIC_KEY
import os

@pytest.fixture(name="session")
def session_fixture():
    db_file = "test_web_push.db"
    if os.path.exists(db_file):
        os.remove(db_file)
    engine = create_engine(f"sqlite:///{db_file}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    if os.path.exists(db_file):
        os.remove(db_file)

@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session
    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()

def test_get_push_config(client: TestClient):
    response = client.get("/accounts/push-config")
    assert response.status_code == 200
    assert response.json()["public_key"] == VAPID_PUBLIC_KEY

def test_subscribe_push(client: TestClient, session: Session):
    sub_data = {
        "endpoint": "https://push-service.com/123",
        "p256dh": "fake_p256dh",
        "auth": "fake_auth"
    }
    
    # Action: Subscribe
    response = client.post("/accounts/subscribe-push", json=sub_data)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Verify in DB
    sub = session.exec(select(PushSubscription).where(PushSubscription.endpoint == sub_data["endpoint"])).first()
    assert sub is not None
    assert sub.p256dh == "fake_p256dh"
    assert sub.auth == "fake_auth"
    
    # Action: Subscribe again (update)
    sub_data["p256dh"] = "updated_p256dh"
    response = client.post("/accounts/subscribe-push", json=sub_data)
    assert response.status_code == 200
    
    session.refresh(sub)
    assert sub.p256dh == "updated_p256dh"

def test_unsubscribe_push(client: TestClient, session: Session):
    # Setup: Add a subscription
    sub = PushSubscription(endpoint="https://push.com/456", p256dh="key", auth="auth")
    session.add(sub)
    session.commit()
    
    # Action: Unsubscribe
    sub_data = {
        "endpoint": "https://push.com/456",
        "p256dh": "key",
        "auth": "auth"
    }
    response = client.post("/accounts/unsubscribe-push", json=sub_data)
    assert response.status_code == 200
    
    # Verify deleted
    deleted_sub = session.exec(select(PushSubscription).where(PushSubscription.endpoint == "https://push.com/456")).first()
    assert deleted_sub is None

from sqlmodel import select
