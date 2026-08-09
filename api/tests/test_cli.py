import getpass

from sqlalchemy import select

from app import cli
from app.config import get_settings
from app.models import AuthSession, User
from app.security.passwords import verify_password
from tests.conftest import DEFAULT_PASSWORD

BOOTSTRAP_PASSWORD = "bootstrap owner password"


def feed(monkeypatch, email: str, passwords: list[str]) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": email)
    answers = iter(passwords)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))


def test_create_owner_success(db, monkeypatch, capsys) -> None:
    feed(monkeypatch, "Owner@Example.com", [BOOTSTRAP_PASSWORD, BOOTSTRAP_PASSWORD])
    assert cli.create_owner(db) == 0

    user = db.scalar(select(User))
    assert user.email == "owner@example.com"  # normalized
    assert user.role == "owner"
    assert user.is_active is True
    assert user.must_change_password is True
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password(user.password_hash, BOOTSTRAP_PASSWORD)

    output = capsys.readouterr()
    assert BOOTSTRAP_PASSWORD not in output.out + output.err
    assert user.password_hash not in output.out + output.err


def test_create_owner_rejects_duplicate(db, make_user, monkeypatch) -> None:
    make_user(email="owner@example.com")
    feed(monkeypatch, "owner@example.com", [BOOTSTRAP_PASSWORD, BOOTSTRAP_PASSWORD])
    assert cli.create_owner(db) == 1
    assert db.scalar(select(User).where(User.email == "owner@example.com")) is not None


def test_create_owner_rejects_invalid_email(db, monkeypatch) -> None:
    feed(monkeypatch, "not-an-email", [])
    assert cli.create_owner(db) == 1
    assert db.scalar(select(User)) is None


def test_create_owner_rejects_weak_password(db, monkeypatch) -> None:
    feed(monkeypatch, "owner@example.com", ["weak"])
    assert cli.create_owner(db) == 1
    assert db.scalar(select(User)) is None


def test_create_owner_rejects_password_mismatch(db, monkeypatch) -> None:
    feed(monkeypatch, "owner@example.com", [BOOTSTRAP_PASSWORD, "a different password"])
    assert cli.create_owner(db) == 1
    assert db.scalar(select(User)) is None


def test_emergency_reset_revokes_sessions_and_forces_change(db, make_user, monkeypatch, capsys):
    from app.services.auth import create_session

    user = make_user(email="user@example.com")
    create_session(db, user, get_settings())
    db.commit()

    new_temp = "emergency temp password"
    feed(monkeypatch, "user@example.com", [new_temp, new_temp])
    assert cli.reset_password(db) == 0

    db.expire_all()
    user = db.scalar(select(User))
    assert user.must_change_password is True
    assert user.password_changed_at is not None
    assert verify_password(user.password_hash, new_temp)
    assert not verify_password(user.password_hash, DEFAULT_PASSWORD)
    session = db.scalar(select(AuthSession))
    assert session.revoked_at is not None

    output = capsys.readouterr()
    assert new_temp not in output.out + output.err


def test_emergency_reset_unknown_email(db, monkeypatch) -> None:
    feed(monkeypatch, "missing@example.com", [])
    assert cli.reset_password(db) == 1
