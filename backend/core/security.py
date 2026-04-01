import secrets
import hashlib
import base64

def generate_pkce_verifier():
    return secrets.token_urlsafe(64)

def generate_pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).decode('utf-8').replace('=', '')
