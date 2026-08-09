"""Business logo: verification, normalization and durable storage.

Nothing that was uploaded is ever stored or served. The bytes are decoded to
prove they really are one of the accepted raster formats, checked against
bounded dimensions, stripped of metadata, corrected for orientation and
re-encoded by the server into a single safe static format. What reaches
PostgreSQL — and later a browser — is only ever the image this module
produced.
"""

import hashlib
import io
import logging

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.models import CommunicationSettings, utcnow
from app.services.leads import LeadError

logger = logging.getLogger(__name__)

# The uploaded bytes may be at most this large. Enforced pre-parse by
# BodyLimitMiddleware as well; this is the second line.
MAX_UPLOAD_BYTES = 1024 * 1024

# Formats Pillow may decode here. Anything else — SVG, HTML, PDF, ICO — is
# refused, and the decision is made from decoded content, never a filename or
# a client-supplied content type.
ACCEPTED_FORMATS = {"PNG", "JPEG", "WEBP"}

# A logo is chrome, not artwork: these bounds keep a decompression bomb from
# ever being allocated, and keep the stored row small.
MAX_SOURCE_DIMENSION = 4000
MAX_SOURCE_PIXELS = 8_000_000
MAX_STORED_DIMENSION = 512

# One output format for everything, so a browser is never asked to sniff and
# transparency survives.
STORED_MIME = "image/png"

# Pillow's own guard against decompression bombs, kept well under our own
# pixel ceiling so the DecompressionBombError path is unreachable in practice.
Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS


class BrandingError(LeadError):
    """Rejected logo upload; the message is safe to show the uploader."""


def _reject(message: str) -> BrandingError:
    return BrandingError(message, status_code=400)


def normalize_logo(raw: bytes) -> tuple[bytes, str, int, int, str]:
    """Turn uploaded bytes into the image we are willing to store.

    Returns (bytes, mime, width, height, digest). Raises BrandingError with a
    message the uploader can act on; never leaks decoder internals.
    """
    if not raw:
        raise _reject("Choose an image file to upload.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise BrandingError("The image must be 1 MB or smaller.", status_code=413)

    # First pass: verify() proves the file is structurally a real image of a
    # format we accept. It consumes the parser, so the image is reopened after.
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            source_format = probe.format
            width, height = probe.size
            frames = getattr(probe, "n_frames", 1)
            probe.verify()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        # Pillow reports a malformed chunk as SyntaxError, which is not an
        # OSError; letting it escape would surface as a 500.
        SyntaxError,
        Image.DecompressionBombError,
    ) as error:
        # The uploader gets a plain answer; the decoder's reason is not theirs.
        logger.info("Rejected logo upload: %s", type(error).__name__)
        raise _reject("That file is not a readable PNG, JPEG or WebP image.") from error

    if source_format not in ACCEPTED_FORMATS:
        raise _reject("Upload a PNG, JPEG or WebP image.")
    if frames > 1:
        raise _reject("Animated images are not supported. Upload a still image.")
    if width <= 0 or height <= 0:
        raise _reject("That image has no usable dimensions.")
    if width > MAX_SOURCE_DIMENSION or height > MAX_SOURCE_DIMENSION:
        raise _reject(
            f"The image is too large: keep both sides under {MAX_SOURCE_DIMENSION} pixels."
        )
    if width * height > MAX_SOURCE_PIXELS:
        raise _reject("The image has too many pixels. Use a smaller version.")

    # Second pass: actually decode, then rebuild the image from its pixels so
    # nothing from the original container survives into what we store.
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            # Applies and then discards the EXIF orientation tag, so the stored
            # image is upright without carrying the tag that made it so.
            oriented = ImageOps.exif_transpose(source) or source
            converted = oriented.convert("RGBA")
            converted.thumbnail((MAX_STORED_DIMENSION, MAX_STORED_DIMENSION), Image.LANCZOS)
            # Rebuilt from raw pixels, so no info dict, EXIF block or ICC
            # profile from the original can ride along into what we store.
            clean = Image.frombytes("RGBA", converted.size, converted.tobytes())
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        logger.info("Rejected logo upload during decode: %s", type(error).__name__)
        raise _reject("That image could not be processed. Try exporting it again.") from error

    buffer = io.BytesIO()
    # No exif/icc/pnginfo argument is passed, so nothing but pixels is written.
    clean.save(buffer, format="PNG", optimize=True)
    stored = buffer.getvalue()
    digest = hashlib.sha256(stored).hexdigest()
    return stored, STORED_MIME, clean.width, clean.height, digest


def set_logo(db: Session, settings_row: CommunicationSettings, raw: bytes) -> CommunicationSettings:
    stored, mime, width, height, digest = normalize_logo(raw)
    settings_row.logo_bytes = stored
    settings_row.logo_mime = mime
    settings_row.logo_width = width
    settings_row.logo_height = height
    settings_row.logo_digest = digest
    settings_row.logo_updated_at = utcnow()
    db.flush()
    return settings_row


def clear_logo(db: Session, settings_row: CommunicationSettings) -> CommunicationSettings:
    settings_row.logo_bytes = None
    settings_row.logo_mime = None
    settings_row.logo_width = None
    settings_row.logo_height = None
    settings_row.logo_digest = None
    settings_row.logo_updated_at = utcnow()
    db.flush()
    return settings_row


def initials(business_name: str) -> str:
    """Fallback wordmark: at most two letters from the business name."""
    words = [word for word in (business_name or "").split() if word[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[1][:1]).upper()
