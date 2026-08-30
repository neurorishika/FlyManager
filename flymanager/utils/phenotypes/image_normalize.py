import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 1024
WEBP_QUALITY = 75
MAX_INPUT_PIXELS = 50_000_000
CONTENT_TYPE = "image/webp"


class ImageRejected(ValueError):
    """Raised when input is not a usable image."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    content_type: str
    width: int
    height: int
    sha256: str
    bytes: int


def normalize_image(raw, *, max_bytes=MAX_UPLOAD_BYTES):
    raw = bytes(raw or b"")
    if not raw:
        raise ImageRejected("empty upload")
    if max_bytes is not None and len(raw) > max_bytes:
        raise ImageRejected(f"image is larger than {max_bytes // (1024 * 1024)} MB")
    already_normalized = False
    source_size = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as probe:
                if probe.width * probe.height > MAX_INPUT_PIXELS:
                    raise ImageRejected("image has too many pixels")
                source_size = probe.size
                already_normalized = (
                    probe.format == "WEBP"
                    and max(probe.size) <= MAX_DIMENSION
                    and not probe.getexif()
                    and not probe.info.get("icc_profile")
                    and not probe.info.get("xmp")
                )
                probe.verify()
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, ValueError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageRejected("file is not a readable image") from exc
    if already_normalized:
        digest = hashlib.sha256(raw).hexdigest()
        return NormalizedImage(raw, CONTENT_TYPE, source_size[0], source_size[1],
                               digest, len(raw))

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_INPUT_PIXELS:
                raise ImageRejected("image has too many pixels")
            image = image.convert("RGB")
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=6)
            width, height = image.size
    except ImageRejected:
        raise
    except (OSError, ValueError) as exc:
        raise ImageRejected("image could not be re-encoded") from exc
    data = buffer.getvalue()
    return NormalizedImage(data, CONTENT_TYPE, width, height,
                           hashlib.sha256(data).hexdigest(), len(data))
