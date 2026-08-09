from app.security.tokens import digest_token, generate_token


def test_tokens_are_unique_and_long() -> None:
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100
    # 32 bytes of entropy url-safe encoded is at least 43 characters.
    assert all(len(token) >= 43 for token in tokens)


def test_digest_is_deterministic_per_pepper() -> None:
    token = generate_token()
    assert digest_token(token, "pepper-a") == digest_token(token, "pepper-a")
    assert digest_token(token, "pepper-a") != digest_token(token, "pepper-b")
    assert digest_token(generate_token(), "pepper-a") != digest_token(token, "pepper-a")


def test_digest_does_not_contain_token() -> None:
    token = generate_token()
    digest = digest_token(token, "pepper")
    assert token not in digest
    assert len(digest) == 64  # SHA-256 hex
