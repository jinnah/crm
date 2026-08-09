from app.security.passwords import hash_password, validate_password, verify_password


def test_hash_uses_argon2id_and_verifies() -> None:
    password = "correct horse battery staple"
    hashed = hash_password(password)
    assert hashed.startswith("$argon2id$")
    assert password not in hashed
    assert verify_password(hashed, password)
    assert not verify_password(hashed, "wrong password entirely")


def test_hashes_are_salted() -> None:
    password = "correct horse battery staple"
    assert hash_password(password) != hash_password(password)


def test_verify_handles_garbage_hash() -> None:
    assert not verify_password("not-a-hash", "anything at all")


def test_policy_minimum_length() -> None:
    assert validate_password("elevenchars") is not None
    assert validate_password("twelve chars") is None


def test_policy_maximum_length() -> None:
    assert validate_password("x" * 128) is None
    assert validate_password("x" * 129) is not None


def test_policy_rejects_whitespace_only() -> None:
    assert validate_password(" " * 12) is not None
    assert validate_password("\t\n " * 6) is not None


def test_policy_allows_spaces_and_unicode() -> None:
    assert validate_password("pässwörter sind gut") is None
    assert validate_password("日本語のパスワードですよ") is None


def test_policy_has_no_composition_rules() -> None:
    assert validate_password("alllowercaseletters") is None
    assert validate_password("123456789012") is None


def test_policy_does_not_trim() -> None:
    # Leading/trailing spaces count toward length and are preserved.
    assert validate_password("  ten ch  ") is not None  # 10 chars with spaces
    assert validate_password("  valid password  ") is None
