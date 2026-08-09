from datetime import timedelta

from fastapi import Response
from sqlalchemy import select

from app.api.v1.auth import _set_session_cookie
from app.config import Settings
from app.models import AuthSession, utcnow
from tests.conftest import DEFAULT_PASSWORD, csrf_headers, login

NEW_PASSWORD = "an entirely new password"


def test_login_success_sets_cookie_and_returns_user(client, make_user) -> None:
    make_user(email="owner@example.com")
    response = login(client, "owner@example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["user"]["role"] == "owner"
    assert body["csrf_token"]
    # No security internals in the payload.
    assert "password_hash" not in body["user"]
    assert "token_digest" not in body

    set_cookie = response.headers["set-cookie"]
    assert "crm_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie


def test_secure_cookie_flag_in_production_config() -> None:
    response = Response()
    _set_session_cookie(response, "token", Settings(session_cookie_secure=True))
    assert "Secure" in response.headers["set-cookie"]
    response = Response()
    _set_session_cookie(response, "token", Settings(session_cookie_secure=False))
    assert "Secure" not in response.headers["set-cookie"]


def test_login_failures_are_generic(client, make_user) -> None:
    make_user(email="known@example.com")
    make_user(email="inactive@example.com", is_active=False)

    wrong_password = login(client, "known@example.com", "totally wrong password")
    unknown_email = login(client, "unknown@example.com")
    inactive = login(client, "inactive@example.com")

    assert wrong_password.status_code == unknown_email.status_code == inactive.status_code == 401
    assert wrong_password.json() == unknown_email.json() == inactive.json()


def test_login_email_is_case_insensitive(client, make_user) -> None:
    make_user(email="owner@example.com")
    assert login(client, "OWNER@Example.COM").status_code == 200


def test_session_endpoint(client, make_user) -> None:
    assert client.get("/api/v1/auth/session").status_code == 401
    make_user(email="owner@example.com")
    login(client, "owner@example.com")
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "owner@example.com"


def test_logout_requires_csrf_and_revokes_session(client, make_user) -> None:
    make_user(email="owner@example.com")
    logged_in = login(client, "owner@example.com")

    assert client.post("/api/v1/auth/logout").status_code == 403  # missing CSRF token
    bad = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"})
    assert bad.status_code == 403

    response = client.post("/api/v1/auth/logout", headers=csrf_headers(logged_in))
    assert response.status_code == 204
    assert client.get("/api/v1/auth/session").status_code == 401


def test_disallowed_origin_is_rejected(client, make_user) -> None:
    make_user(email="owner@example.com")
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": DEFAULT_PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403

    allowed = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": DEFAULT_PASSWORD},
        headers={"Origin": "http://localhost:3000"},
    )
    assert allowed.status_code == 200


def test_absolute_session_expiry(client, make_user, db) -> None:
    make_user(email="owner@example.com")
    login(client, "owner@example.com")
    session = db.scalar(select(AuthSession))
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/v1/auth/session").status_code == 401


def test_inactivity_timeout(client, make_user, db) -> None:
    make_user(email="owner@example.com")
    login(client, "owner@example.com")
    session = db.scalar(select(AuthSession))
    session.last_activity_at = utcnow() - timedelta(hours=9)
    db.commit()
    assert client.get("/api/v1/auth/session").status_code == 401
    db.expire_all()
    assert db.scalar(select(AuthSession)).revoked_at is not None


def test_login_rate_limit_locks_out(client, make_user) -> None:
    make_user(email="owner@example.com")
    for _ in range(5):
        assert login(client, "owner@example.com", "wrong password here").status_code == 401
    locked = login(client, "owner@example.com")  # correct password, but locked out
    assert locked.status_code == 429
    assert "password" not in locked.json()["detail"].lower()


def test_forced_password_change_blocks_protected_actions(client, make_user) -> None:
    make_user(email="owner@example.com", must_change_password=True)
    logged_in = login(client, "owner@example.com")
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["must_change_password"] is True

    # Blocked from the protected application even as an owner.
    assert client.get("/api/v1/users").status_code == 403

    # Session endpoint and password change remain reachable.
    assert client.get("/api/v1/auth/session").status_code == 200
    change = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": NEW_PASSWORD},
        headers=csrf_headers(logged_in),
    )
    assert change.status_code == 204
    assert client.get("/api/v1/users").status_code == 200


def test_change_password_validations(client, make_user) -> None:
    make_user(email="owner@example.com")
    logged_in = login(client, "owner@example.com")
    headers = csrf_headers(logged_in)

    wrong_current = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "not the password", "new_password": NEW_PASSWORD},
        headers=headers,
    )
    assert wrong_current.status_code == 400

    too_short = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "short"},
        headers=headers,
    )
    assert too_short.status_code == 400

    reuse = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert reuse.status_code == 400
    assert "different" in reuse.json()["detail"]

    no_csrf = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert no_csrf.status_code == 403


def test_change_password_rotates_and_revokes_other_sessions(client, app, make_user, db) -> None:
    from fastapi.testclient import TestClient

    make_user(email="owner@example.com")
    other_client = TestClient(app)
    login(other_client, "owner@example.com")  # second device
    logged_in = login(client, "owner@example.com")
    old_cookie = client.cookies.get("crm_session")

    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": NEW_PASSWORD},
        headers=csrf_headers(logged_in),
    )
    assert response.status_code == 204
    assert client.cookies.get("crm_session") != old_cookie

    # The rotated session works; the other device's session is revoked.
    assert client.get("/api/v1/auth/session").status_code == 200
    assert other_client.get("/api/v1/auth/session").status_code == 401

    # New password works, old does not.
    fresh = TestClient(app)
    assert login(fresh, "owner@example.com", NEW_PASSWORD).status_code == 200
    assert login(fresh, "owner@example.com", DEFAULT_PASSWORD).status_code == 401
