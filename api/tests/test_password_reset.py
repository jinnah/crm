from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.models import PasswordResetToken, utcnow
from tests.conftest import DEFAULT_PASSWORD, login

NEW_PASSWORD = "a brand new reset password"


def request_reset(client, email: str):
    return client.post("/api/v1/auth/forgot-password", json={"email": email})


def token_from_mailer(mailer) -> str:
    _, reset_url = mailer.sent[-1]
    return parse_qs(urlparse(reset_url).query)["token"][0]


def reset(client, token: str, new_password: str = NEW_PASSWORD):
    return client.post(
        "/api/v1/auth/reset-password", json={"token": token, "new_password": new_password}
    )


def test_forgot_password_does_not_reveal_account_existence(client, make_user, mailer) -> None:
    make_user(email="known@example.com")
    existing = request_reset(client, "known@example.com")
    missing = request_reset(client, "unknown@example.com")
    assert existing.status_code == missing.status_code == 202
    assert existing.json() == missing.json()
    assert existing.headers["referrer-policy"] == "no-referrer"
    # Mail goes out only for the real account.
    assert [to for to, _ in mailer.sent] == ["known@example.com"]


def test_no_email_for_inactive_account(client, make_user, mailer) -> None:
    make_user(email="inactive@example.com", is_active=False)
    assert request_reset(client, "inactive@example.com").status_code == 202
    assert mailer.sent == []


def test_reset_link_never_returned_in_response(client, make_user, mailer) -> None:
    make_user(email="known@example.com")
    response = request_reset(client, "known@example.com")
    token = token_from_mailer(mailer)
    assert token not in response.text


def test_full_reset_flow_invalidates_sessions_and_tokens(client, app, make_user, mailer) -> None:
    from fastapi.testclient import TestClient

    make_user(email="user@example.com")
    login(client, "user@example.com")
    assert client.get("/api/v1/auth/session").status_code == 200

    request_reset(client, "user@example.com")
    token = token_from_mailer(mailer)
    assert reset(client, token).status_code == 204

    # Existing session is revoked by the reset.
    assert client.get("/api/v1/auth/session").status_code == 401

    # New password works; token is single-use.
    fresh = TestClient(app)
    assert login(fresh, "user@example.com", NEW_PASSWORD).status_code == 200
    assert login(fresh, "user@example.com", DEFAULT_PASSWORD).status_code == 401
    assert reset(client, token).status_code == 400


def test_reset_password_policy_enforced(client, make_user, mailer) -> None:
    make_user(email="user@example.com")
    request_reset(client, "user@example.com")
    token = token_from_mailer(mailer)
    assert reset(client, token, "short").status_code == 400
    assert reset(client, token, " " * 20).status_code == 400
    assert reset(client, token).status_code == 204


def test_invalid_and_expired_tokens_rejected(client, make_user, mailer, db) -> None:
    make_user(email="user@example.com")
    assert reset(client, "completely-made-up-token").status_code == 400

    request_reset(client, "user@example.com")
    token = token_from_mailer(mailer)
    row = db.scalar(select(PasswordResetToken))
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert reset(client, token).status_code == 400


def test_new_token_invalidates_previous(client, make_user, mailer) -> None:
    make_user(email="user@example.com")
    request_reset(client, "user@example.com")
    first_token = token_from_mailer(mailer)
    request_reset(client, "user@example.com")
    second_token = token_from_mailer(mailer)

    assert reset(client, first_token).status_code == 400
    assert reset(client, second_token).status_code == 204


def test_recovery_rate_limit(client, make_user) -> None:
    make_user(email="user@example.com")
    for _ in range(3):
        assert request_reset(client, "user@example.com").status_code == 202
    limited = request_reset(client, "user@example.com")
    assert limited.status_code == 429
    # Response stays generic — no account-existence information.
    assert "user@example.com" not in limited.text


def test_mail_delivery_failure_is_safe_and_generic(client, make_user, mailer) -> None:
    make_user(email="user@example.com")
    mailer.fail = True
    response = request_reset(client, "user@example.com")
    assert response.status_code == 202
    assert "fail" not in response.text.lower()
