import os
import pytest
from unittest.mock import patch, MagicMock
from backend.core.config import get_requested_scopes

@patch("backend.core.config.Session")
@patch("backend.core.config.engine")
def test_get_requested_scopes_no_env(mock_engine, mock_session_class):
    # Mock database to return no setting
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.first.return_value = None

    # Mock os.getenv to return None for ENABLE_DELETION_SCOPE
    with patch("os.getenv", return_value=None):
        scopes = get_requested_scopes()
        assert "https://www.googleapis.com/auth/gmail.modify" in scopes
        assert "https://mail.google.com/" not in scopes

@patch("backend.core.config.Session")
@patch("backend.core.config.engine")
def test_get_requested_scopes_env_false(mock_engine, mock_session_class):
    # Mock os.getenv to return False
    with patch("os.getenv", return_value="false"):
        scopes = get_requested_scopes()
        assert "https://www.googleapis.com/auth/gmail.modify" in scopes
        assert "https://mail.google.com/" not in scopes

@patch("backend.core.config.Session")
@patch("backend.core.config.engine")
def test_get_requested_scopes_env_true(mock_engine, mock_session_class):
    # Mock os.getenv to return true
    with patch("os.getenv", return_value="true"):
        scopes = get_requested_scopes()
        assert "https://mail.google.com/" in scopes
        assert "https://www.googleapis.com/auth/gmail.modify" not in scopes

@patch("backend.core.config.Session")
@patch("backend.core.config.engine")
def test_get_requested_scopes_db_true(mock_engine, mock_session_class):
    # Mock database to return ENABLE_DELETION_SCOPE=true
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_setting = MagicMock()
    mock_setting.value = "true"
    mock_session.exec.return_value.first.return_value = mock_setting

    # Mock os.getenv to return None for ENABLE_DELETION_SCOPE
    with patch("os.getenv", return_value=None):
        scopes = get_requested_scopes()
        assert "https://mail.google.com/" in scopes
        assert "https://www.googleapis.com/auth/gmail.modify" not in scopes
