from sqlalchemy import select

from app.models import User
from tests.conftest import csrf_headers, login

TEMP_PASSWORD = "temporary password 123"


def owner_session(client, make_user, email="owner@example.com"):
    make_user(email=email, role="owner")
    response = login(client, email)
    return csrf_headers(response)


def create_user_via_api(client, headers, email, role="team_member"):
    return client.post(
        "/api/v1/users",
        json={"email": email, "role": role, "temporary_password": TEMP_PASSWORD},
        headers=headers,
    )


def test_owner_can_list_users(client, make_user) -> None:
    headers = owner_session(client, make_user)
    response = client.get("/api/v1/users", headers=headers)
    assert response.status_code == 200
    assert [u["email"] for u in response.json()] == ["owner@example.com"]


def test_non_owners_are_rejected(client, make_user) -> None:
    make_user(email="manager@example.com", role="manager")
    make_user(email="member@example.com", role="team_member")

    for email in ("manager@example.com", "member@example.com"):
        logged_in = login(client, email)
        headers = csrf_headers(logged_in)
        assert client.get("/api/v1/users", headers=headers).status_code == 403
        assert create_user_via_api(client, headers, "new@example.com").status_code == 403
        client.post("/api/v1/auth/logout", headers=headers)


def test_owner_creates_user_with_temporary_password(client, make_user, db) -> None:
    headers = owner_session(client, make_user)
    response = create_user_via_api(client, headers, "New.Person@Example.COM")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.person@example.com"  # normalized
    assert body["must_change_password"] is True
    assert "password_hash" not in body

    # Plaintext is never persisted; only an Argon2id hash is stored.
    row = db.scalar(select(User).where(User.email == "new.person@example.com"))
    assert row.password_hash.startswith("$argon2id$")
    assert TEMP_PASSWORD not in row.password_hash

    # The new user can log in (case-insensitively) but must change the password.
    fresh_login = login(client, "NEW.PERSON@example.com", TEMP_PASSWORD)
    assert fresh_login.status_code == 200
    assert fresh_login.json()["user"]["must_change_password"] is True


def test_create_user_validations(client, make_user) -> None:
    headers = owner_session(client, make_user)
    duplicate = create_user_via_api(client, headers, "owner@example.com")
    assert duplicate.status_code == 409

    bad_role = client.post(
        "/api/v1/users",
        json={"email": "x@example.com", "role": "admin", "temporary_password": TEMP_PASSWORD},
        headers=headers,
    )
    assert bad_role.status_code == 400

    weak = client.post(
        "/api/v1/users",
        json={"email": "x@example.com", "role": "manager", "temporary_password": "weak"},
        headers=headers,
    )
    assert weak.status_code == 400


def test_owner_updates_role_and_active_state(client, app, make_user) -> None:
    from fastapi.testclient import TestClient

    headers = owner_session(client, make_user)
    created = create_user_via_api(client, headers, "member@example.com").json()

    promoted = client.patch(
        f"/api/v1/users/{created['id']}", json={"role": "manager"}, headers=headers
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "manager"

    # Deactivation revokes the target's sessions.
    target_client = TestClient(app)
    target_login = login(target_client, "member@example.com", TEMP_PASSWORD)
    assert target_login.status_code == 200
    deactivated = client.patch(
        f"/api/v1/users/{created['id']}", json={"is_active": False}, headers=headers
    )
    assert deactivated.status_code == 200
    assert target_client.get("/api/v1/auth/session").status_code == 401
    assert login(target_client, "member@example.com", TEMP_PASSWORD).status_code == 401

    reactivated = client.patch(
        f"/api/v1/users/{created['id']}", json={"is_active": True}, headers=headers
    )
    assert reactivated.status_code == 200
    assert login(target_client, "member@example.com", TEMP_PASSWORD).status_code == 200


def test_cannot_remove_last_active_owner(client, make_user) -> None:
    headers = owner_session(client, make_user)
    me = client.get("/api/v1/auth/session").json()["user"]

    demote = client.patch(f"/api/v1/users/{me['id']}", json={"role": "manager"}, headers=headers)
    assert demote.status_code == 409
    deactivate = client.patch(
        f"/api/v1/users/{me['id']}", json={"is_active": False}, headers=headers
    )
    assert deactivate.status_code == 409

    # With a second active owner the same change is allowed.
    create_user_via_api(client, headers, "second-owner@example.com", role="owner")
    demote_now = client.patch(
        f"/api/v1/users/{me['id']}", json={"role": "manager"}, headers=headers
    )
    assert demote_now.status_code == 200


def test_no_hard_delete_endpoint(client, make_user) -> None:
    headers = owner_session(client, make_user)
    me = client.get("/api/v1/auth/session").json()["user"]
    assert client.delete(f"/api/v1/users/{me['id']}", headers=headers).status_code == 405


def test_admin_password_reset_revokes_sessions_and_forces_change(client, app, make_user) -> None:
    from fastapi.testclient import TestClient

    headers = owner_session(client, make_user)
    created = create_user_via_api(client, headers, "member@example.com").json()

    target_client = TestClient(app)
    login(target_client, "member@example.com", TEMP_PASSWORD)
    # Clear the forced flag so we can prove the admin reset re-sets it.
    target_headers = csrf_headers(login(target_client, "member@example.com", TEMP_PASSWORD))
    target_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEMP_PASSWORD, "new_password": "member chosen password"},
        headers=target_headers,
    )
    assert target_client.get("/api/v1/auth/session").status_code == 200

    new_temp = "another temp password"
    response = client.post(
        f"/api/v1/users/{created['id']}/reset-password",
        json={"temporary_password": new_temp},
        headers=headers,
    )
    assert response.status_code == 204
    assert target_client.get("/api/v1/auth/session").status_code == 401

    relogin = login(target_client, "member@example.com", new_temp)
    assert relogin.status_code == 200
    assert relogin.json()["user"]["must_change_password"] is True


def test_users_endpoints_require_csrf(client, make_user) -> None:
    owner_session(client, make_user)
    response = client.post(
        "/api/v1/users",
        json={"email": "x@example.com", "role": "manager", "temporary_password": TEMP_PASSWORD},
    )
    assert response.status_code == 403
