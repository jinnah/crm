from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

# argon2-cffi's defaults use Argon2id with current recommended parameters.
_hasher = PasswordHasher()

# Verified against when the account lookup fails, so unknown and known
# emails take a comparable amount of time on login.
_DUMMY_HASH = _hasher.hash("dummy-timing-equalizer")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def burn_verification_time(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def validate_password(password: str) -> str | None:
    """Return an error message when the password violates policy, else None.

    Policy: 12–128 characters, spaces and Unicode allowed, no composition
    rules, no trimming or transformation, not whitespace-only.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters long."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters long."
    if password.strip() == "":
        return "Password cannot consist only of whitespace."
    return None
