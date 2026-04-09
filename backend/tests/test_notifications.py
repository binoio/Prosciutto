import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from backend.main import app
from backend.db import get_session
from backend.models import Account, NewMailNotification
from sqlmodel import Session, SQLModel, create_engine
import os
from datetime import datetime

@pytest.fixture(name="session")
def session_fixture():
    db_file = "test_notifications.db"
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

def test_toggle_notifications(client: TestClient, session: Session):
    # Setup: Add an account
    account = Account(email="notif@example.com", credentials_json='{"token": "fake"}', notifications_enabled=False)
    session.add(account)
    session.commit()
    
    # Action: Toggle notifications ON
    response = client.patch(f"/accounts/{account.id}/toggle-notifications", json={"is_active": True})
    assert response.status_code == 200
    assert response.json()["notifications_enabled"] == True
    
    # Verify in DB
    updated_account = session.get(Account, account.id)
    assert updated_account.notifications_enabled == True
    
    # Action: Toggle notifications OFF
    response = client.patch(f"/accounts/{account.id}/toggle-notifications", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["notifications_enabled"] == False
    assert session.get(Account, account.id).notifications_enabled == False

@patch("backend.services.gmail_service.get_gmail_service")
@patch("backend.services.gmail_service.get_detailed_messages_batch")
def test_check_new_messages_internal_logic(mock_get_detailed, mock_get_service, client: TestClient, session: Session):
    # Setup: Add an account with notifications enabled
    account = Account(
        email="poll@example.com", 
        credentials_json='{"token": "fake"}', 
        is_active=True,
        notifications_enabled=True,
        last_history_id="1000"
    )
    session.add(account)
    session.commit()
    
    # Mock Gmail Service
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    
    # 1. Mock profile to return a NEW historyId
    mock_service.users().getProfile().execute.return_value = {"historyId": "1100"}
    
    # 2. Mock history.list to return one new message
    mock_service.users().history().list().execute.return_value = {
        "history": [
            {
                "id": "1100",
                "messagesAdded": [
                    {
                        "message": {
                            "id": "msg123",
                            "labelIds": ["INBOX", "UNREAD"]
                        }
                    }
                ]
            }
        ]
    }
    
    # 3. Mock detailed batch fetch
    mock_get_detailed.return_value = [
        {"id": "msg123", "subject": "Test Subject", "from": "sender@test.com"}
    ]
    
    # Action: Check new messages via internal testing endpoint
    response = client.get("/accounts/internal/check-new-messages")
    assert response.status_code == 200
    messages = response.json()
    
    assert len(messages) == 1
    assert messages[0]["id"] == "msg123"
    assert messages[0]["subject"] == "Test Subject"
    
    # Verify account history_id was updated in DB
    session.refresh(account)
    assert account.last_history_id == "1100"

def test_check_new_messages_endpoint(client: TestClient, session: Session):
    # Setup: Add a notification to the table
    notif = NewMailNotification(
        message_id="msg456",
        account_id=1,
        account_email="test@example.com",
        subject="Hello",
        sender="someone@test.com",
        discovered_at=datetime.utcnow()
    )
    session.add(notif)
    session.commit()
    
    # Action: Call endpoint
    response = client.get("/accounts/check-new-messages")
    assert response.status_code == 200
    data = response.json()
    
    assert len(data) == 1
    assert data[0]["id"] == "msg456"
    assert data[0]["subject"] == "Hello"

@patch("backend.services.gmail_service.get_gmail_service")
def test_check_new_messages_no_change_logic(mock_get_service, client: TestClient, session: Session):
    # Setup: Account with history_id "1000"
    account = Account(
        email="nochange@example.com", 
        credentials_json='{"token": "fake"}', 
        is_active=True,
        notifications_enabled=True,
        last_history_id="1000"
    )
    session.add(account)
    session.commit()
    
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    
    # Mock profile to return SAME historyId
    mock_service.users().getProfile().execute.return_value = {"historyId": "1000"}
    
    # Action: Call internal testing endpoint
    response = client.get("/accounts/internal/check-new-messages")
    assert response.status_code == 200
    assert response.json() == []
    
    # History ID should remain "1000"
    session.refresh(account)
    assert account.last_history_id == "1000"
