"""Installation-time administration commands.

Run inside the API environment:

    uv run python -m app.cli create-owner
    uv run python -m app.cli reset-password

Both commands prompt interactively; passwords use hidden input, are never
accepted as command-line arguments, and are never printed or logged.
"""

import argparse
import getpass
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.db import get_sessionmaker
from app.models import User
from app.security.passwords import hash_password, validate_password
from app.services.auth import get_user_by_email, normalize_email, revoke_all_sessions

_email_adapter = TypeAdapter(EmailStr)


def _prompt_email() -> str | None:
    raw = input("Email: ").strip()
    try:
        _email_adapter.validate_python(raw)
    except ValidationError:
        print("Error: invalid email address.", file=sys.stderr)
        return None
    return normalize_email(raw)


def _prompt_password() -> str | None:
    password = getpass.getpass("Temporary password (hidden): ")
    policy_error = validate_password(password)
    if policy_error is not None:
        print(f"Error: {policy_error}", file=sys.stderr)
        return None
    confirmation = getpass.getpass("Confirm password (hidden): ")
    if password != confirmation:
        print("Error: passwords do not match.", file=sys.stderr)
        return None
    return password


def create_owner(db: Session) -> int:
    """Create the first (or an additional) active owner with a temporary password."""
    email = _prompt_email()
    if email is None:
        return 1
    if get_user_by_email(db, email) is not None:
        print("Error: an account with this email already exists.", file=sys.stderr)
        return 1
    password = _prompt_password()
    if password is None:
        return 1
    db.add(
        User(
            email=email,
            password_hash=hash_password(password),
            role="owner",
            is_active=True,
            must_change_password=True,
        )
    )
    db.commit()
    print(f"Owner account created for {email}. The password must be changed at first login.")
    return 0


def reset_password(db: Session) -> int:
    """Emergency reset: set a temporary password and revoke the user's sessions."""
    email = _prompt_email()
    if email is None:
        return 1
    user = get_user_by_email(db, email)
    if user is None:
        print("Error: no account found for this email.", file=sys.stderr)
        return 1
    password = _prompt_password()
    if password is None:
        return 1
    user.password_hash = hash_password(password)
    user.must_change_password = True
    revoke_all_sessions(db, user.id)
    db.commit()
    print(f"Password reset for {email}. All sessions revoked; change required at next login.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("create-owner", help="Create an active owner account")
    subparsers.add_parser("reset-password", help="Emergency password reset for an account")
    args = parser.parse_args(argv)

    db = get_sessionmaker()()
    try:
        if args.command == "create-owner":
            return create_owner(db)
        return reset_password(db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
