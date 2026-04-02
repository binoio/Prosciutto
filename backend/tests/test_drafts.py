import pytest
import os
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
from backend.main import app
from backend.db import get_session
from backend.models import Account, Setting
from sqlmodel import Session, SQLModel, create_engine

@pytest.fixture(name="session")
def session_fixture():
    db_file = "test_drafts.db"
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

@patch("backend.routes.messages.get_gmail_service")
def test_save_draft(mock_get_service, client):
    # Mock account
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    
    # Mock draft create
    mock_service.users().drafts().create().execute.return_value = {"id": "draft123"}
    
    response = client.post("/accounts/1/drafts", json={
        "to": "test@example.com",
        "subject": "Test Draft",
        "body": "This is a draft",
        "isHtml": False
    })
    
    assert response.status_code == 200
    assert response.json()["message"] == "Draft created"
    assert response.json()["draft"]["id"] == "draft123"
    
    # Verify mock call
    assert mock_service.users().drafts().create.called

@patch("backend.routes.messages.get_gmail_service")
def test_update_draft(mock_get_service, client):
    # Mock account
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    
    # Mock draft update
    mock_service.users().drafts().update().execute.return_value = {"id": "draft123"}
    
    response = client.post("/accounts/1/drafts", json={
        "to": "test@example.com",
        "subject": "Updated Draft",
        "body": "This is updated",
        "isHtml": False,
        "draftId": "draft123"
    })
    
    assert response.status_code == 200
    assert response.json()["message"] == "Draft updated"
    assert response.json()["draft"]["id"] == "draft123"
    
    # Verify mock call
    assert mock_service.users().drafts().update.called

@patch("backend.routes.messages.get_gmail_service")
def test_send_email_deletes_draft(mock_get_service, client):
    # Mock account
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    
    # Mock message send
    mock_service.users().messages().send().execute.return_value = {"id": "msg123"}
    # Mock draft delete
    mock_service.users().drafts().delete().execute.return_value = {}
    
    response = client.post("/accounts/1/send", json={
        "to": "test@example.com",
        "subject": "Test Send",
        "body": "Sent with draft",
        "isHtml": False,
        "draftId": "draft123"
    })
    
    assert response.status_code == 200
    assert response.json()["message"] == "Email sent"
    
    # Verify draft delete call
    mock_service.users().drafts().delete.assert_any_call(userId="me", id="draft123")
