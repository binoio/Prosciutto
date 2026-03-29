import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from backend.main import app, get_session
from backend.models import Account
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool
import os

@pytest.fixture(name="session")
def session_fixture():
    db_file = "test_extended.db"
    if os.path.exists(db_file):
        os.remove(db_file)
    engine = create_engine(f"sqlite:///{db_file}")
    from backend.main import cache
    cache.clear()
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
def test_unread_dot_presence_backend(mock_creds_class, mock_build, client: TestClient, session: Session):
    # Add an account
    account = Account(email="test@example.com", credentials_json='{"token": "fake"}', is_active=True)
    session.add(account)
    session.commit()
    
    mock_creds = MagicMock()
    mock_creds.expired = False
    mock_creds_class.from_authorized_user_info.return_value = mock_creds
    
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    
    # Mock list: one unread message
    mock_service.users().messages().list().execute.return_value = {"messages": [{"id": "m1"}]}
    
    # Mock detail: includes 'UNREAD' label
    detail_response = {
        "id": "m1",
        "snippet": "Hello",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "123456",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Test Subject"},
                {"name": "From", "value": "sender@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"}
            ]
        }
    }
    
    def new_batch_http_request(callback):
        return MockBatch(callback, {"m1": detail_response})
    
    mock_service.new_batch_http_request.side_effect = new_batch_http_request
    
    # Check individual account messages
    response = client.get(f"/accounts/{account.id}/messages")
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 1
    assert "UNREAD" in data["messages"][0]["labelIds"]
    
    # Check unified messages
    response = client.get("/unified/messages")
    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 1
    assert "UNREAD" in data["messages"][0]["labelIds"]

def test_unread_dot_presence_frontend():
    # Since we can't easily run a browser, we'll check the index.html content 
    # to ensure the unread-dot logic is present and correct.
    index_path = os.path.join(os.path.dirname(__file__), "../../frontend/index.html")
    with open(index_path, "r") as f:
        content = f.read()
    
    # Check if the unread-dot div exists with the correct logic
    assert 'class="unread-dot ${isUnread ? \'\' : \'invisible\'}"' in content
    assert "const isUnread = msg.labelIds && msg.labelIds.includes('UNREAD');" in content
    
    # Check if the CSS for unread-dot exists and is blue
    css_path = os.path.join(os.path.dirname(__file__), "../../frontend/styles/styles.css")
    with open(css_path, "r") as f:
        css_content = f.read()
    
    assert ".unread-dot {" in css_content
    assert "background-color: #1a73e8;" in css_content # Blue

@patch("backend.main.build")
@patch("backend.main.Credentials")
def test_unified_inbox_message_count(mock_creds_class, mock_build, client: TestClient, session: Session):
    # Add two accounts
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
    
    # Mock account 1: 0 messages
    # Mock account 2: 20 messages
    def list_side_effect(**kwargs):
        userId = kwargs.get("userId")
        # We need to distinguish between accounts.
        # But mock_service is shared.
        # Actually, get_gmail_service is called for each account.
        # Since we patched 'build', it returns the SAME mock_service for both accounts.
        # This is a bit tricky. We should probably return DIFFERENT services for each account.
        return MagicMock()

    # Re-patch to handle different accounts
    with patch("backend.main.get_gmail_service") as mock_get_service:
        s1 = MagicMock()
        s2 = MagicMock()
        mock_get_service.side_effect = [s1, s2]
        
        # S1: 0 messages
        s1.users().messages().list().execute.return_value = {"messages": []}
        
        # S2: 20 messages, but mock will return based on maxResults if provided
        def list_execute_s2(**kwargs):
            # Wait, list() is called with kwargs, then execute() is called.
            # So s2.users().messages().list.call_args should have maxResults.
            pass

        # S2: 50 messages, but mock will return based on maxResults if provided
        def list_mock_s2(**kwargs):
            max_results = kwargs.get("maxResults", 100)
            m = MagicMock()
            m.execute.return_value = {
                "messages": [{"id": f"m{i}"} for i in range(min(50, max_results))]
            }
            return m

        s2.users().messages().list.side_effect = list_mock_s2

        # Mock detailed messages for S2
        detail_responses = {f"m{i}": {"id": f"m{i}", "internalDate": str(1000 - i)} for i in range(50)}

        def new_batch_http_request_2(callback):
            return MockBatch(callback, detail_responses)

        s2.new_batch_http_request.side_effect = new_batch_http_request_2

        response = client.get("/unified/messages")
        assert response.status_code == 200
        data = response.json()

        # The user says "account 1 inbox has 0 messages, account 2 inbox has 20 messages, unified inbox has 10 messages"
        # This is because maxResults was set to 10 in unified_messages.
        # If we want it to be ACCURATE, it should be 50 if there are 50.
        assert len(data["messages"]) == 50

