import pytest
import os
from fastapi.testclient import TestClient
from backend.main import app, get_session
from backend.models import Account, Setting
from sqlmodel import Session, SQLModel, create_engine
from unittest.mock import patch

@pytest.fixture(name="session")
def session_fixture():
    db_file = "test_settings.db"
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

def test_get_settings_no_env(client: TestClient, session: Session):
    # Mock os.getenv to return None for credentials
    with patch("os.getenv", side_effect=lambda k, d=None: d if k not in ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"] else None):
        response = client.get("/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["GOOGLE_CLIENT_ID"] == ""
        assert data["is_client_id_env"] is False
        assert data["THEME"] == "automatic"
        assert data["COMPOSE_NEW_WINDOW"] == "true"
        assert data["WARN_BEFORE_DELETE"] == "true"

def test_update_warn_before_delete_setting(client: TestClient, session: Session):
    response = client.post("/settings", json={"WARN_BEFORE_DELETE": "false"})
    assert response.status_code == 200
    
    # Verify DB
    setting = session.query(Setting).filter(Setting.key == "WARN_BEFORE_DELETE").first()
    assert setting.value == "false"
    
    # Verify GET
    response = client.get("/settings")
    assert response.json()["WARN_BEFORE_DELETE"] == "false"

def test_update_compose_window_setting(client: TestClient, session: Session):
    response = client.post("/settings", json={"COMPOSE_NEW_WINDOW": "false"})
    assert response.status_code == 200
    
    # Verify DB
    setting = session.query(Setting).filter(Setting.key == "COMPOSE_NEW_WINDOW").first()
    assert setting.value == "false"
    
    # Verify GET
    response = client.get("/settings")
    assert response.json()["COMPOSE_NEW_WINDOW"] == "false"

def test_get_settings_with_env(client: TestClient, session: Session):
    # Mock os.getenv
    def mock_getenv(key, default=None):
        if key == "GOOGLE_CLIENT_ID": return "env_id"
        if key == "GOOGLE_CLIENT_SECRET": return "env_secret"
        return default

    with patch("os.getenv", side_effect=mock_getenv):
        response = client.get("/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["GOOGLE_CLIENT_ID"] == "env_id"
        assert data["is_client_id_env"] is True

def test_update_settings_respects_env(client: TestClient, session: Session):
    # Set env var
    def mock_getenv(key, default=None):
        if key == "GOOGLE_CLIENT_ID": return "env_id"
        return default

    with patch("os.getenv", side_effect=mock_getenv):
        # Try to update GOOGLE_CLIENT_ID
        client.post("/settings", json={"GOOGLE_CLIENT_ID": "new_id", "THEME": "dark"})
        
        # Check DB
        setting_id = session.query(Setting).filter(Setting.key == "GOOGLE_CLIENT_ID").first()
        assert setting_id is None # Should not have been added to DB because env is set
        
        setting_theme = session.query(Setting).filter(Setting.key == "THEME").first()
        assert setting_theme.value == "dark"

def test_delete_account(client: TestClient, session: Session):
    account = Account(email="delete@example.com", credentials_json='{}')
    session.add(account)
    session.commit()
    account_id = account.id
    
    response = client.delete(f"/accounts/{account_id}")
    assert response.status_code == 200
    assert response.json() == {"message": "Account deleted"}
    
    # Verify deleted from DB
    assert session.get(Account, account_id) is None

def test_delete_account_not_found(client: TestClient):
    response = client.delete("/accounts/999")
    assert response.status_code == 404
