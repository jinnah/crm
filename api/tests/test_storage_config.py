"""S3 client construction honors the configured addressing style (no network
access — boto3 builds clients offline)."""

from app.config import Settings
from app.services.storage import S3Storage


def make_storage(style: str | None = None) -> S3Storage:
    kwargs = {
        "documents_storage_backend": "s3",
        "documents_s3_bucket": "unit-test-bucket",
        "documents_s3_endpoint_url": "http://s3.unit.test:9000",
        "documents_s3_region": "us-east-1",
        "documents_s3_access_key_id": "unit-test-key",
        "documents_s3_secret_access_key": "unit-test-secret",
    }
    if style is not None:
        kwargs["documents_s3_addressing_style"] = style
    return S3Storage(Settings(**kwargs))


def test_default_addressing_style_is_auto():
    storage = make_storage()
    assert storage.client.meta.config.s3["addressing_style"] == "auto"


def test_path_and_virtual_styles_reach_the_client():
    for style in ("path", "virtual"):
        storage = make_storage(style)
        assert storage.client.meta.config.s3["addressing_style"] == style
