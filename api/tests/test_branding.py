"""Business logo: ownership, real image verification, normalization, serving."""

import io

from PIL import Image

from app.services import branding
from tests.conftest import csrf_headers, login


def owner_session(client, make_user):
    make_user(email="owner@example.com", role="owner")
    return csrf_headers(login(client, "owner@example.com"))


def png_bytes(width=200, height=120, mode="RGBA", color=(20, 80, 160, 255)) -> bytes:
    image = Image.new(mode, (width, height), color[: len(mode)])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(width=200, height=120, exif: bytes | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (200, 60, 40))
    buffer = io.BytesIO()
    if exif is not None:
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")
    return buffer.getvalue()


def upload(client, headers, data, content_type="image/png"):
    return client.post(
        "/api/v1/settings/branding/logo",
        content=data,
        headers={**headers, "Content-Type": content_type},
    )


# --- ownership -----------------------------------------------------------


def test_only_an_owner_may_change_the_logo(client, make_user) -> None:
    """The test client keeps one cookie jar, so each role signs in in turn."""
    make_user(email="owner@example.com", role="owner")
    make_user(email="manager@example.com", role="manager")
    make_user(email="tech@example.com", role="team_member")

    manager = csrf_headers(login(client, "manager@example.com"))
    assert upload(client, manager, png_bytes()).status_code == 403
    assert client.delete("/api/v1/settings/branding/logo", headers=manager).status_code == 403

    member = csrf_headers(login(client, "tech@example.com"))
    assert upload(client, member, png_bytes()).status_code == 403
    # A team member may still read the metadata: the shell they already see
    # renders the logo.
    assert client.get("/api/v1/settings/branding", headers=member).status_code == 200

    owner = csrf_headers(login(client, "owner@example.com"))
    assert upload(client, owner, png_bytes()).status_code == 200


def test_an_anonymous_visitor_cannot_upload(client, make_user) -> None:
    owner_session(client, make_user)
    client.post("/api/v1/auth/logout", headers={"Origin": "http://localhost:3000"})
    response = client.post(
        "/api/v1/settings/branding/logo",
        content=png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code in (401, 403)


# --- content verification -----------------------------------------------


def test_a_real_png_is_accepted_and_normalized(client, db, make_user) -> None:
    headers = owner_session(client, make_user)
    response = upload(client, headers, png_bytes(300, 150))
    assert response.status_code == 200
    body = response.json()
    assert body["has_logo"] is True
    assert (body["width"], body["height"]) == (300, 150)

    from app.models import CommunicationSettings

    row = db.scalar(__import__("sqlalchemy").select(CommunicationSettings))
    db.refresh(row)
    assert row.logo_mime == "image/png"
    assert len(row.logo_digest) == 64
    # Whatever came in, a PNG is what is stored.
    assert row.logo_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_a_jpeg_is_re_encoded_rather_than_stored_as_sent(client, db, make_user) -> None:
    headers = owner_session(client, make_user)
    original = jpeg_bytes()
    assert original.startswith(b"\xff\xd8\xff")  # a genuine JPEG went in
    assert upload(client, headers, original, "image/jpeg").status_code == 200

    from sqlalchemy import select

    from app.models import CommunicationSettings

    row = db.scalar(select(CommunicationSettings))
    db.refresh(row)
    assert row.logo_bytes != original, "the uploaded bytes must never be what is stored"
    assert row.logo_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_svg_and_html_are_refused_however_they_are_labelled(client, make_user) -> None:
    headers = owner_session(client, make_user)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    html = b'<html><body onload="alert(1)">hello</body></html>'

    # Claiming to be a PNG changes nothing: the decision comes from the bytes.
    for payload, label in (
        (svg, "image/svg+xml"),
        (svg, "image/png"),
        (html, "image/png"),
        (b"GIF89a" + b"\x00" * 40, "image/gif"),
        (b"%PDF-1.4\n" + b"\x00" * 40, "application/pdf"),
    ):
        response = upload(client, headers, payload, label)
        assert response.status_code == 400, f"{label} payload was not refused"
        assert "PNG" in response.json()["detail"]


def test_a_spoofed_extension_and_mime_type_do_not_help(client, make_user) -> None:
    headers = owner_session(client, make_user)
    # Executable content wearing an image content type.
    payload = b"MZ\x90\x00" + b"\x00" * 200
    assert upload(client, headers, payload, "image/webp").status_code == 400


def test_a_truncated_image_is_refused(client, make_user) -> None:
    headers = owner_session(client, make_user)
    truncated = png_bytes()[:40]
    assert upload(client, headers, truncated).status_code == 400


def test_a_corrupt_image_body_is_refused(client, make_user) -> None:
    headers = owner_session(client, make_user)
    data = bytearray(png_bytes(120, 120))
    # Keep the signature, wreck the pixel data.
    for index in range(60, len(data)):
        data[index] = 0
    assert upload(client, headers, bytes(data)).status_code == 400


def test_an_empty_body_is_refused(client, make_user) -> None:
    headers = owner_session(client, make_user)
    assert upload(client, headers, b"").status_code == 400


def test_an_animated_image_is_refused(client, make_user) -> None:
    headers = owner_session(client, make_user)
    frames = [Image.new("RGB", (60, 60), (index * 40, 0, 0)) for index in range(3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="WEBP", save_all=True, append_images=frames[1:], duration=100)
    response = upload(client, headers, buffer.getvalue(), "image/webp")
    assert response.status_code == 400
    assert "Animated" in response.json()["detail"]


# --- bounds --------------------------------------------------------------


def test_extreme_dimensions_are_refused(client, make_user) -> None:
    headers = owner_session(client, make_user)
    # A tall, thin image compresses to very little but exceeds the side bound.
    tall = png_bytes(8, branding.MAX_SOURCE_DIMENSION + 10, mode="L", color=(0,))
    response = upload(client, headers, tall)
    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


def test_an_oversized_body_is_rejected_while_streaming(client, make_user) -> None:
    """The ceiling is enforced before the endpoint runs, so the image is never
    decoded and the handler never sees it."""
    headers = owner_session(client, make_user)
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * (branding.MAX_UPLOAD_BYTES + 1024)

    def chunks():
        view = memoryview(payload)
        for start in range(0, len(payload), 64 * 1024):
            yield bytes(view[start : start + 64 * 1024])

    response = client.post(
        "/api/v1/settings/branding/logo",
        content=chunks(),
        headers={**headers, "Content-Type": "image/png"},
    )
    assert response.status_code == 413
    # Nothing was stored.
    assert client.get("/api/v1/settings/branding", headers=headers).json()["has_logo"] is False


def test_a_large_image_is_scaled_down_for_storage(client, make_user) -> None:
    headers = owner_session(client, make_user)
    response = upload(client, headers, png_bytes(1600, 800))
    assert response.status_code == 200
    body = response.json()
    assert max(body["width"], body["height"]) == branding.MAX_STORED_DIMENSION
    assert body["width"] == 512 and body["height"] == 256  # aspect ratio kept


# --- normalization -------------------------------------------------------


def test_metadata_is_stripped_and_orientation_applied() -> None:
    """EXIF is not carried over, and the rotation it described is baked in."""
    landscape = Image.new("RGB", (100, 40), (10, 20, 30))
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees
    exif[271] = "SecretCameraMake"
    landscape.save(buffer, format="JPEG", exif=exif)
    source = buffer.getvalue()
    assert b"SecretCameraMake" in source

    stored, mime, width, height, digest = branding.normalize_logo(source)

    assert mime == "image/png"
    # Orientation 6 turns the landscape source into a portrait image.
    assert (width, height) == (40, 100)
    assert b"SecretCameraMake" not in stored
    with Image.open(io.BytesIO(stored)) as result:
        assert result.format == "PNG"
        assert not result.getexif()
    assert len(digest) == 64


def test_transparency_survives_normalization() -> None:
    transparent = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
    transparent.putpixel((10, 10), (255, 0, 0, 255))
    buffer = io.BytesIO()
    transparent.save(buffer, format="PNG")

    stored, _, _, _, _ = branding.normalize_logo(buffer.getvalue())
    with Image.open(io.BytesIO(stored)) as result:
        assert result.mode == "RGBA"
        assert result.getpixel((0, 0))[3] == 0
        assert result.getpixel((10, 10))[:3] == (255, 0, 0)


def test_identical_uploads_produce_the_same_digest() -> None:
    source = png_bytes(64, 64)
    first = branding.normalize_logo(source)
    second = branding.normalize_logo(source)
    assert first[4] == second[4]


def test_initials_fall_back_sensibly() -> None:
    assert branding.initials("Northside Roofing & HVAC") == "NR"
    assert branding.initials("Acme") == "AC"
    assert branding.initials("") == "?"
    assert branding.initials("   ") == "?"


# --- serving -------------------------------------------------------------


def test_the_logo_is_served_publicly_with_caching_headers(client, make_user) -> None:
    headers = owner_session(client, make_user)
    upload(client, headers, png_bytes(120, 60))

    response = client.get("/api/v1/public/logo")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "max-age" in response.headers["cache-control"]
    etag = response.headers["etag"]
    assert etag.startswith('"') and len(etag) == 66

    # A conditional request is answered without resending the bytes.
    revalidated = client.get("/api/v1/public/logo", headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.content == b""


def test_replacing_the_logo_changes_the_etag(client, make_user) -> None:
    headers = owner_session(client, make_user)
    upload(client, headers, png_bytes(120, 60, color=(10, 10, 10, 255)))
    first = client.get("/api/v1/public/logo").headers["etag"]

    upload(client, headers, png_bytes(120, 60, color=(240, 240, 240, 255)))
    second = client.get("/api/v1/public/logo").headers["etag"]
    assert first != second, "a replaced logo must not be served from cache"

    # The old ETag no longer revalidates.
    assert client.get("/api/v1/public/logo", headers={"If-None-Match": first}).status_code == 200


def test_removing_the_logo_leaves_a_clean_fallback(client, make_user) -> None:
    headers = owner_session(client, make_user)
    upload(client, headers, png_bytes())
    removed = client.delete("/api/v1/settings/branding/logo", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["has_logo"] is False

    assert client.get("/api/v1/public/logo").status_code == 404
    public = client.get("/api/v1/public/branding").json()
    assert public["has_logo"] is False
    assert public["initials"]


def test_public_branding_exposes_nothing_private(client, make_user) -> None:
    headers = owner_session(client, make_user)
    client.patch(
        "/api/v1/settings/communication",
        json={
            "business_name": "Northside Roofing",
            "alert_destination_phone": "+15550100999",
            "alert_template": "Internal only {{lead_name}}",
        },
        headers=headers,
    )
    response = client.get("/api/v1/public/branding")
    assert response.status_code == 200
    assert set(response.json()) == {
        "business_name",
        "has_logo",
        "width",
        "height",
        "updated_at",
        "initials",
    }
    assert "+15550100999" not in response.text
    assert "Internal only" not in response.text


def test_settings_responses_do_not_embed_the_image(client, make_user) -> None:
    headers = owner_session(client, make_user)
    upload(client, headers, png_bytes(400, 400))
    for path in ("/api/v1/settings/communication", "/api/v1/settings/branding"):
        body = client.get(path, headers=headers).text
        assert "logo_bytes" not in body
        assert "data:image" not in body
        assert len(body) < 4000, f"{path} looks like it carries image data"
