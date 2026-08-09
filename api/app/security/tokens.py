import hashlib
import hmac
import secrets

# 32 bytes = 256 bits of cryptographically secure randomness.
_TOKEN_BYTES = 32


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def digest_token(token: str, pepper: str) -> str:
    """Keyed digest stored in place of the raw token, so a database leak
    alone does not expose usable session or reset tokens."""
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
