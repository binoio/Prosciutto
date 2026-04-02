import pytest
import threading
import time
import uvicorn
import re
from playwright.sync_api import Page, expect
from backend.main import app
from backend.db import get_session
from backend.models import Account, Setting
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool
import os

# Port for the live server
PORT = 8001

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")

@pytest.fixture(scope="module", autouse=True)
def server():
    # Setup test database
    db_file = "test_frontend.db"
    if os.path.exists(db_file):
        os.remove(db_file)
    engine = create_engine(f"sqlite:///{db_file}")
    SQLModel.metadata.create_all(engine)
    
    # Pre-fill settings and an account
    with Session(engine) as session:
        session.add(Setting(key="GOOGLE_CLIENT_ID", value="test_id"))
        session.add(Setting(key="GOOGLE_CLIENT_SECRET", value="test_secret"))
        session.add(Setting(key="COMPOSE_NEW_WINDOW", value="false"))
        fake_creds = '{"token": "fake", "refresh_token": "fake", "client_id": "test_id", "client_secret": "test_secret"}'
        session.add(Account(id=1, email="test@example.com", credentials_json=fake_creds, is_active=True))
        session.commit()

    def get_session_override():
        return Session(engine)
    
    app.dependency_overrides[get_session] = get_session_override
    
    # Mock gmail service to return empty labels
    from unittest.mock import patch, MagicMock
    mock_service = MagicMock()
    mock_service.users().labels().list().execute.return_value = {"labels": []}
    mock_service.users().messages().list().execute.return_value = {"messages": []}
    
    patcher = patch("backend.routes.messages.get_gmail_service", return_value=mock_service)
    patcher.start()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    time.sleep(2)  # Wait for server to start
    yield
    patcher.stop()
    app.dependency_overrides.clear()
    if os.path.exists(db_file):
        os.remove(db_file)

def test_compose_cc_bcc_toggle(page: Page):
    page.on("console", lambda msg: print(f"BROWSER CONSOLE: {msg.text}"))
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"http://127.0.0.1:{PORT}/")
    
    # Click Compose
    page.click("button.compose-btn:has-text('+ Compose')")
    
    # Wait for panel to open
    page.wait_for_selector("#toggle-cc", state="visible")
    
    # Check CC/BCC toggles exist
    expect(page.locator("#toggle-cc")).to_be_visible()
    expect(page.locator("#toggle-bcc")).to_be_visible()
    
    # Check CC/BCC groups are hidden initially
    expect(page.locator("#group-cc")).to_be_hidden()
    expect(page.locator("#group-bcc")).to_be_hidden()
    
    # Click Cc
    page.click("#toggle-cc", force=True)
    time.sleep(1)
    
    # Check CC group is visible and toggle is hidden
    expect(page.locator("#group-cc")).to_be_visible()
    expect(page.locator("#toggle-cc")).to_be_hidden()
    
    # Click Bcc
    page.click("#toggle-bcc", force=True)
    time.sleep(1)
    
    # Check BCC group is visible and toggle is hidden
    expect(page.locator("#group-bcc")).to_be_visible()
    expect(page.locator("#toggle-bcc")).to_be_hidden()
