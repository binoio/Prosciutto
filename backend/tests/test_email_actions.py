import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from backend.main import app
from backend.db import get_session
from backend.models import Account
from sqlmodel import Session, SQLModel, create_engine
import os
import base64

@pytest.fixture(name="session")
def session_fixture():
    db_file = "test_actions.db"
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

@patch("backend.services.gmail_service.build")
@patch("backend.services.gmail_service.Credentials")
def test_get_message_detailed_headers(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    
    text_data = base64.urlsafe_b64encode("Body".encode()).decode()
    
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg123",
        "threadId": "thread456",
        "snippet": "Snippet",
        "internalDate": "123456789",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Test Subject"},
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Cc", "value": "cc@example.com"},
                {"name": "Message-ID", "value": "<msg-id-123@google.com>"},
                {"name": "References", "value": "<ref-id-000@google.com>"}
            ],
            "body": {"data": text_data}
        }
    }
    
    response = client.get(f"/accounts/{account.id}/messages/msg123")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "msg123"
    assert data["threadId"] == "thread456"
    assert data["messageId"] == "<msg-id-123@google.com>"
    assert data["references"] == "<ref-id-000@google.com>"
    assert data["to"] == "me@example.com"
    assert data["cc"] == "cc@example.com"

@patch("backend.services.gmail_service.build")
@patch("backend.services.gmail_service.Credentials")
def test_send_email_with_threading(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().send().execute.return_value = {"id": "sent123", "threadId": "thread456"}
    
    payload = {
        "to": "dest@example.com",
        "subject": "Re: Hi",
        "body": "This is a reply",
        "threadId": "thread456",
        "inReplyTo": "<msg-id-123@google.com>",
        "references": "<ref-id-000@google.com> <msg-id-123@google.com>"
    }
    
    response = client.post(f"/accounts/{account.id}/send", json=payload)
    assert response.status_code == 200
    assert response.json()["message"] == "Email sent"
    
    # Verify that the Gmail API was called with the correct threadId
    _, kwargs = mock_service.users.return_value.messages.return_value.send.call_args
    sent_body = kwargs['body']
    assert sent_body['threadId'] == "thread456"
    
    # Verify that the raw message contains the threading headers
    raw_msg = base64.urlsafe_b64decode(sent_body['raw']).decode()
    assert "In-Reply-To: <msg-id-123@google.com>" in raw_msg
    assert "References: <ref-id-000@google.com> <msg-id-123@google.com>" in raw_msg

@patch("backend.services.gmail_service.build")
@patch("backend.services.gmail_service.Credentials")
def test_reply_all_threading(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().send().execute.return_value = {"id": "sent789", "threadId": "thread456"}
    
    # Simulate a reply-all payload
    payload = {
        "to": "sender@example.com, cc@example.com",
        "subject": "Re: Test Subject",
        "body": "Replying to all",
        "threadId": "thread456",
        "inReplyTo": "<msg-id-123@google.com>",
        "references": "<ref-id-000@google.com> <msg-id-123@google.com>"
    }
    
    response = client.post(f"/accounts/{account.id}/send", json=payload)
    assert response.status_code == 200
    
    _, kwargs = mock_service.users.return_value.messages.return_value.send.call_args
    sent_body = kwargs['body']
    raw_msg = base64.urlsafe_b64decode(sent_body['raw']).decode()
    
    assert "to: sender@example.com, cc@example.com" in raw_msg.lower()
    assert "in-reply-to: <msg-id-123@google.com>" in raw_msg.lower()
    assert "references: <ref-id-000@google.com> <msg-id-123@google.com>" in raw_msg.lower()

@patch("backend.services.gmail_service.build")
@patch("backend.services.gmail_service.Credentials")
def test_delete_message(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().delete().execute.return_value = {}
    
    response = client.delete(f"/accounts/{account.id}/messages/msg123")
    assert response.status_code == 200
    assert response.json()["message"] == "Message permanently deleted"
    
    # Verify Gmail API call
    mock_service.users().messages().delete.assert_called_with(userId="me", id="msg123")

@patch("backend.services.gmail_service.build")
@patch("backend.services.gmail_service.Credentials")
def test_batch_delete_messages(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().batchDelete().execute.return_value = {}
    
    payload = {"ids": ["msg1", "msg2"]}
    response = client.post(f"/accounts/{account.id}/messages/batch-delete", json=payload)
    assert response.status_code == 200
    assert response.json()["message"] == "Successfully deleted 2 messages"
    
    # Verify Gmail API call
    mock_service.users().messages().batchDelete.assert_called_with(userId="me", body={"ids": ["msg1", "msg2"]})
