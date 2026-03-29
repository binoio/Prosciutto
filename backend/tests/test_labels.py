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
    db_file = "test_labels.db"
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

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_list_labels(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().labels().list().execute.return_value = {
        "labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "Label_1", "name": "My Label", "type": "user"}
        ]
    }
    
    response = client.get(f"/accounts/{account.id}/labels")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "My Label"

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_empty_label(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    
    # First call to list messages
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }
    
    response = client.delete(f"/accounts/{account.id}/labels/TRASH/empty")
    assert response.status_code == 200
    assert "Emptied 2 messages" in response.json()["message"]
    
    assert mock_service.users().messages().batchDelete.called

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_empty_unified_label(mock_creds_class, mock_build, client: TestClient, session: Session):
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
    
    response = client.delete("/unified/labels/SPAM/empty")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_create_label(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().labels().create().execute.return_value = {
        "id": "Label_2",
        "name": "New Label",
        "type": "user"
    }
    
    response = client.post(f"/accounts/{account.id}/labels", json={"name": "New Label"})
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New Label"
    assert data["id"] == "Label_2"
    
    # Verify Gmail API call
    mock_service.users().labels().create.assert_called()
    _, kwargs = mock_service.users().labels().create.call_args
    assert kwargs["body"]["name"] == "New Label"

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_apply_label_to_message(mock_creds_class, mock_build, client: TestClient, session: Session):
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}')
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().batchModify.return_value.execute.return_value = {}
    
    payload = {
        "ids": ["msg123"],
        "addLabelIds": ["Label_123"]
    }
    
    response = client.post(f"/accounts/{account.id}/messages/batch-modify", json=payload)
    assert response.status_code == 200
    
    # Verify Gmail API call
    mock_service.users().messages().batchModify.assert_called_once()
    _, kwargs = mock_service.users().messages().batchModify.call_args
    assert kwargs["body"]["ids"] == ["msg123"]
    assert kwargs["body"]["addLabelIds"] == ["Label_123"]
