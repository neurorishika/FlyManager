import io

import pytest
from PIL import Image

from flymanager.utils.phenotypes.image_normalize import ImageRejected, MAX_UPLOAD_BYTES, normalize_image


def _png(size=(640, 480), mode="RGB"):
    out = io.BytesIO()
    Image.new(mode, size).save(out, format="PNG")
    return out.getvalue()


def test_normalizes_bounds_and_is_idempotent():
    result = normalize_image(_png((4000, 2000)))
    assert (result.width, result.height) == (1024, 512)
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"
    assert normalize_image(result.data).sha256 == result.sha256


@pytest.mark.parametrize("mode", ["RGBA", "P", "L"])
def test_converts_common_modes(mode):
    assert normalize_image(_png((32, 32), mode)).content_type == "image/webp"


def test_rejects_empty_invalid_and_oversized_input():
    for raw in (b"", b"not an image", b"x" * (MAX_UPLOAD_BYTES + 1)):
        with pytest.raises(ImageRejected):
            normalize_image(raw)


def test_strips_exif_metadata():
    """Uploads can carry GPS and camera data; none of it should be stored."""
    buffer = io.BytesIO()
    image = Image.new("RGB", (64, 64))
    exif = image.getexif()
    exif[271] = "SecretCamera"   # Make
    exif[272] = "SecretModel"    # Model
    image.save(buffer, format="JPEG", exif=exif)

    assert Image.open(io.BytesIO(buffer.getvalue())).getexif(), "fixture has no EXIF"

    result = normalize_image(buffer.getvalue())
    assert not Image.open(io.BytesIO(result.data)).getexif()
    assert b"SecretCamera" not in result.data
    assert b"SecretModel" not in result.data


def test_rejects_a_decompression_bomb_without_decoding_it():
    """A small file declaring an enormous canvas must not be decoded.

    MAX_INPUT_PIXELS is checked against the header before any pixel work,
    so this stays cheap rather than allocating gigabytes first.
    """
    from flymanager.utils.phenotypes.image_normalize import MAX_INPUT_PIXELS

    side = int(MAX_INPUT_PIXELS ** 0.5) + 500
    buffer = io.BytesIO()
    # A solid single-colour PNG this large compresses to a few KB on disk
    # while claiming ~50M+ pixels in its header.
    Image.new("L", (side, side)).save(buffer, format="PNG")
    payload = buffer.getvalue()
    assert len(payload) < MAX_UPLOAD_BYTES, "bomb fixture must pass the size gate"

    with pytest.raises(ImageRejected):
        normalize_image(payload)


def test_rejects_a_bomb_even_when_the_size_gate_is_disabled():
    """The seed builder passes max_bytes=None; the pixel cap must still hold."""
    from flymanager.utils.phenotypes.image_normalize import MAX_INPUT_PIXELS

    side = int(MAX_INPUT_PIXELS ** 0.5) + 500
    buffer = io.BytesIO()
    Image.new("L", (side, side)).save(buffer, format="PNG")

    with pytest.raises(ImageRejected):
        normalize_image(buffer.getvalue(), max_bytes=None)
