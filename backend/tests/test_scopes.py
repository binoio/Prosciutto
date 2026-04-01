import os
import pytest
from unittest.mock import patch
from backend.core.config import get_requested_scopes

def test_get_requested_scopes_no_env():
    # Mock os.path.exists to return False for .env
    with patch("os.path.exists", side_effect=lambda x: False if x == ".env" else True):
        scopes = get_requested_scopes()
        assert "https://www.googleapis.com/auth/gmail.modify" in scopes
        assert "https://mail.google.com/" not in scopes

def test_get_requested_scopes_env_false():
    # Mock os.path.exists to return True for .env and os.getenv to return False
    with patch("os.path.exists", side_effect=lambda x: True if x == ".env" else False):
        with patch("os.getenv", return_value="false"):
            scopes = get_requested_scopes()
            assert "https://www.googleapis.com/auth/gmail.modify" in scopes
            assert "https://mail.google.com/" not in scopes

def test_get_requested_scopes_env_true():
    # Mock os.path.exists to return True for .env and os.getenv to return true
    with patch("os.path.exists", side_effect=lambda x: True if x == ".env" else False):
        with patch("os.getenv", return_value="true"):
            scopes = get_requested_scopes()
            assert "https://mail.google.com/" in scopes
            assert "https://www.googleapis.com/auth/gmail.modify" not in scopes
