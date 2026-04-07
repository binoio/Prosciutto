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
import os
from unittest.mock import patch, MagicMock, AsyncMock

PORT = 8002

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error")

@pytest.fixture(scope="module", autouse=True)
def server():
    db_file = "test_keyboard.db"
    if os.path.exists(db_file): os.remove(db_file)
    engine = create_engine(f"sqlite:///{db_file}")
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        session.add(Setting(key="GOOGLE_CLIENT_ID", value="test_id"))
        session.add(Setting(key="GOOGLE_CLIENT_SECRET", value="test_secret"))
        session.add(Setting(key="COMPOSE_NEW_WINDOW", value="false"))
        session.add(Setting(key="KEYBOARD_SHORTCUTS_ENABLED", value="true"))
        fake_creds = '{"token": "fake", "refresh_token": "fake", "client_id": "test_id", "client_secret": "test_secret"}'
        session.add(Account(id=1, email="test@example.com", credentials_json=fake_creds, is_active=True))
        session.commit()

    def get_session_override():
        return Session(engine)
    
    app.dependency_overrides[get_session] = get_session_override
    
    mock_messages = [
        {
            "id": "msg1", 
            "threadId": "t1", 
            "snippet": "Snippet 1", 
            "subject": "Subject 1", 
            "from": "sender1@example.com", 
            "internalDate": int(time.time() * 1000), 
            "labelIds": ["INBOX", "UNREAD"],
            "accountId": 1
        },
        {
            "id": "msg2", 
            "threadId": "t2", 
            "snippet": "Snippet 2", 
            "subject": "Subject 2", 
            "from": "sender2@example.com", 
            "internalDate": int(time.time() * 1000) - 10000, 
            "labelIds": ["INBOX"],
            "accountId": 1
        }
    ]
    
    mock_service = MagicMock()
    mock_service.users().messages().list().execute.return_value = {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in mock_messages]}
    mock_service.users().messages().get().execute.side_effect = lambda userId, id, format=None: next(m for m in mock_messages if m["id"] == id)
    mock_service.users().labels().list().execute.return_value = {"labels": []}
    
    async_mock_unified = AsyncMock(return_value={"messages": mock_messages, "nextPageToken": None})
    
    with patch("backend.routes.messages.get_gmail_service", return_value=mock_service), \
         patch("backend.routes.messages.get_unified_messages", async_mock_unified):
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(2)
        yield
        app.dependency_overrides.clear()
        if os.path.exists(db_file): os.remove(db_file)

def test_keyboard_navigation(page: Page):
    page.goto(f"http://127.0.0.1:{PORT}/")
    page.wait_for_selector(".message-item", timeout=10000)
    
    # 1. Test 1-5 shortcuts for mailbox jumping
    page.keyboard.press("2")
    expect(page.locator("#view-name")).to_have_text("Sent")
    
    page.keyboard.press("1")
    expect(page.locator("#view-name")).to_have_text("Inbox")
    
    page.keyboard.press("3")
    expect(page.locator("#view-name")).to_have_text("Drafts")
    
    page.keyboard.press("4")
    expect(page.locator("#view-name")).to_have_text("Trash")
    
    page.keyboard.press("5")
    expect(page.locator("#view-name")).to_have_text("All Mail")

    # 2. Test sidebar toggle with 'b'
    expect(page.locator("#sidebar")).not_to_have_class(re.compile(r"collapsed"))
    page.keyboard.press("b")
    expect(page.locator("#sidebar")).to_have_class(re.compile(r"collapsed"))
    page.keyboard.press("b")
    expect(page.locator("#sidebar")).not_to_have_class(re.compile(r"collapsed"))

    # 3. Test keyboard focus navigation with j/k
    page.keyboard.press("1")
    page.wait_for_selector(".message-item")
    page.keyboard.press("j")
    focused_tag = page.evaluate("document.activeElement.tagName")
    assert focused_tag != "BODY"

    # 4. Test keyboard selection with 'x'
    page.focus("#msg-msg1")
    page.keyboard.press("x")
    expect(page.locator("#msg-msg1 .message-checkbox")).to_be_checked()
    page.keyboard.press("x")
    expect(page.locator("#msg-msg1 .message-checkbox")).not_to_be_checked()

    # 5. Test Settings shortcut ','
    with page.expect_popup() as popup_info:
        page.keyboard.press(",")
    settings_page = popup_info.value
    expect(settings_page).to_have_url(re.compile(r"settings=true"))
    settings_page.close()

    # 6. Test Protection against keyboard shortcuts when input is focused
    page.focus("#search-input")
    page.keyboard.type("2")
    expect(page.locator("#view-name")).not_to_have_text("Sent")
    assert page.input_value("#search-input") == "2"

    # 7. Test Enter to activate focused item
    page.focus("#mailbox-SENT")
    page.keyboard.press("Enter")
    expect(page.locator("#view-name")).to_have_text("Sent")

    # 8. Test ArrowDown and ArrowUp
    page.focus("#mailbox-INBOX")
    page.keyboard.press("ArrowDown")
    focused_id = page.evaluate("document.activeElement.id")
    assert focused_id == "mailbox-SENT"
    
    page.keyboard.press("ArrowUp")
    focused_id = page.evaluate("document.activeElement.id")
    assert focused_id == "mailbox-INBOX"

    # 9. Test Refresh with 'u'
    page.keyboard.press("u")

    # 10. Test Space key selection on message item
    page.focus("#msg-msg1")
    page.keyboard.press(" ")
    expect(page.locator("#msg-msg1 .message-checkbox")).to_be_checked()
    page.keyboard.press(" ")
    expect(page.locator("#msg-msg1 .message-checkbox")).not_to_be_checked()

    # 11. Test ArrowDown through various UI elements
    # Start at sidebar
    page.focus(".compose-btn")
    page.keyboard.press("ArrowDown")
    focused_id = page.evaluate("document.activeElement.id")
    assert focused_id == "mailbox-INBOX"
    
    # Skip some items to reach header
    page.focus("#settings-trigger")
    page.keyboard.press("ArrowDown")
    # Next should be refresh button or first message item depending on DOM order
    focused_id = page.evaluate("document.activeElement.id")
    assert focused_id in ["refresh-btn", "multi-select-checkbox", "msg-msg1"]

    # 12. Test Modal Tab navigation
    # Open settings
    page.click("#settings-trigger")
    # Settings opens in a new tab/window because openSettings uses window.open('_blank')
    # But wait, in the test_keyboard_navigation it says page.expect_popup()
    # Let's handle the popup
    
    # Wait, the test already has a settings popup test. Let's adapt it.
    with page.expect_popup() as popup_info:
        page.keyboard.press(",")
    settings_page = popup_info.value
    settings_page.wait_for_selector(".modal-tab")
    
    # Focus first tab
    settings_page.focus(".modal-tab[data-tab='accounts']")
    settings_page.keyboard.press("ArrowRight")
    expect(settings_page.locator(".modal-tab[data-tab='general']")).to_be_focused()
    
    settings_page.keyboard.press("ArrowLeft")
    expect(settings_page.locator(".modal-tab[data-tab='accounts']")).to_be_focused()
    
    settings_page.keyboard.press("Enter")
    expect(settings_page.locator("#tab-accounts")).to_be_visible()
    
    settings_page.close()

    # 13. Test 'x' shortcut on multi-select checkbox
    page.focus("#multi-select-checkbox")
    page.keyboard.press("x")
    expect(page.locator("#multi-select-checkbox")).to_be_checked()
    expect(page.locator("#msg-msg1 .message-checkbox")).to_be_checked()
    
    page.keyboard.press("x")
    expect(page.locator("#multi-select-checkbox")).not_to_be_checked()
    expect(page.locator("#msg-msg1 .message-checkbox")).not_to_be_checked()
    
    # 14. Test '2' shortcut when focused on a checkbox
    page.focus("#multi-select-checkbox")
    page.keyboard.press("2")
    expect(page.locator("#view-name")).to_have_text("Sent")
