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
