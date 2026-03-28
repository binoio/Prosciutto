import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from backend.main import app, get_session
from backend.models import Account, Setting
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

# Setup in-memory SQLite for testing
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

@pytest.fixture(name="session")
def session_fixture():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)

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
    mock_service.users().messages().get().execute.return_value = {"id": "123", "snippet": "Hello", "threadId": "t1", "internalDate": "123456"}
    
    response = client.get(f"/accounts/{account.id}/messages")
    assert response.status_code == 200
    assert response.json()[0]["snippet"] == "Hello"

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
    mock_service.users().messages().get().execute.return_value = {"id": "m1", "snippet": "Snip", "internalDate": "123456", "threadId": "t1"}
    
    response = client.get("/unified/messages")
    assert response.status_code == 200
    assert len(response.json()) == 2
