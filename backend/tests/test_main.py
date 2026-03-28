import pytest
import os
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from backend.main import app, get_session
from backend.models import Account, Setting
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

@pytest.fixture(name="session")
def session_fixture():
    db_file = "test.db"
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

def test_root(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "html" in response.headers["content-type"]

def test_list_accounts_empty(client: TestClient):
    response = client.get("/accounts")
    assert response.status_code == 200
    assert response.json() == []

def test_update_settings(client: TestClient):
    response = client.post("/settings", json={"GOOGLE_CLIENT_ID": "test_id", "GOOGLE_CLIENT_SECRET": "test_secret"})
    assert response.status_code == 200
    assert response.json() == {"message": "Settings updated"}

class MockBatch:
    def __init__(self, callback, responses):
        self.callback = callback
        self.responses = responses
        self.added = []

    def add(self, request, request_id):
        self.added.append(request_id)

    def execute(self):
        for request_id in self.added:
            response = self.responses.get(request_id)
            if response:
                self.callback(request_id, response, None)
            else:
                self.callback(request_id, None, Exception("Not found"))

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_list_messages(mock_creds_class, mock_build, client: TestClient, session: Session):
    # Add an account
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds.to_json.return_value = '{"token": "new_fake"}'
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().list().execute.return_value = {"messages": [{"id": "123"}]}
    
    detail_response = {"id": "123", "snippet": "Hello", "threadId": "t1", "internalDate": "123456"}
    
    def new_batch_http_request(callback):
        return MockBatch(callback, {"123": detail_response})
    
    mock_service.new_batch_http_request.side_effect = new_batch_http_request
    
    response = client.get(f"/accounts/{account.id}/messages")
    assert response.status_code == 200
    data = response.json()
    assert "messages" in data
    assert len(data["messages"]) == 1
    assert data["messages"][0]["snippet"] == "Hello"

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_send_email(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().send().execute.return_value = {"id": "sent123"}
    
    response = client.post(f"/accounts/{account.id}/send", json={
        "to": "dest@example.com",
        "subject": "Hi",
        "body": "Body"
    })
    assert response.status_code == 200
    assert response.json()["message"] == "Email sent"

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_search_messages(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    
    # Mock search result
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1", "threadId": "t1"}],
        "nextPageToken": "token123"
    }
    
    # Mock detail result
    detail_response = {
        "id": "m1",
        "snippet": "Test search result",
        "threadId": "t1",
        "internalDate": "123456789",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Found it"},
                {"name": "From", "value": "sender@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"}
            ]
        }
    }
    
    def new_batch_http_request(callback):
        return MockBatch(callback, {"m1": detail_response})
    
    mock_service.new_batch_http_request.side_effect = new_batch_http_request
    
    response = client.get(f"/accounts/{account.id}/search?q=test")
    assert response.status_code == 200
    data = response.json()
    assert "messages" in data
    assert len(data["messages"]) == 1
    assert data["messages"][0]["subject"] == "Found it"
    assert data["nextPageToken"] == "token123"

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_unified_inbox(mock_creds_class, mock_build, client: TestClient, session: Session):
    acc1 = Account(email="a1@example.com", credentials_json='{"token": "f1"}', is_active=True)
    acc2 = Account(email="a2@example.com", credentials_json='{"token": "f2"}', is_active=True)
    session.add(acc1)
    session.add(acc2)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().list().execute.return_value = {"messages": [{"id": "m1"}]}
    
    detail_response = {"id": "m1", "snippet": "Snip", "internalDate": "123456", "threadId": "t1"}
    
    def new_batch_http_request(callback):
        return MockBatch(callback, {"m1": detail_response})
    
    mock_service.new_batch_http_request.side_effect = new_batch_http_request
    
    response = client.get("/unified/messages")
    assert response.status_code == 200
    assert len(response.json()["messages"]) == 2

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_get_message(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    
    import base64
    text_data = base64.urlsafe_b64encode("Plain text body".encode()).decode()
    html_data = base64.urlsafe_b64encode("<html><body>HTML body</body></html>".encode()).decode()
    
    # Test 1: Single part message
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg123",
        "snippet": "Hello world snippet",
        "internalDate": "123456789",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Test Subject"},
                {"name": "From", "value": "sender@example.com"}
            ],
            "body": {"data": text_data}
        }
    }
    
    response = client.get(f"/accounts/{account.id}/messages/msg123")
    assert response.status_code == 200
    data = response.json()
    assert data["body"] == "Plain text body"
    assert data["html_body"] == ""

    # Test 2: Multipart message (HTML + Text)
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg456",
        "snippet": "Multipart snippet",
        "internalDate": "123456789",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": "Multipart Subject"},
                {"name": "From", "value": "sender@example.com"}
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": text_data}
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": html_data}
                }
            ]
        }
    }
    
    response = client.get(f"/accounts/{account.id}/messages/msg456")
    assert response.status_code == 200
    data = response.json()
    assert data["body"] == "Plain text body"
    assert data["html_body"] == "<html><body>HTML body</body></html>"
