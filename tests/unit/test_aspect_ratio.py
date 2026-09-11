from io import BytesIO

import pytest
from PIL import Image

from src.services.images.aspect_ratio import (
    is_portrait_3_4,
    normalize_to_portrait_3_4,
)
from src.services.images.beautify import NaturalBeautifyService
from src.services.images.metadata import encode_jpeg
from src.services.profiles import BeautifyProfile


@pytest.mark.parametrize(
    ("source_size", "expected_size", "expected_box"),
    [
        ((900, 1200), (900, 1200), (0, 0, 900, 1200)),
        ((1600, 900), (675, 900), (462, 0, 1137, 900)),
        ((1200, 1200), (900, 1200), (150, 0, 1050, 1200)),
        ((900, 1600), (900, 1200), (0, 200, 900, 1400)),
    ],
)
def test_normalize_to_portrait_3_4_uses_largest_center_crop(
    source_size: tuple[int, int],
    expected_size: tuple[int, int],
    expected_box: tuple[int, int, int, int],
) -> None:
    source = Image.new("RGB", source_size, "white")

    result = normalize_to_portrait_3_4(source)

    assert result.output_size == expected_size
    assert result.crop_box == expected_box
    assert is_portrait_3_4(*result.output_size)
    assert source.size == source_size


def test_normalization_audit_preserves_uploaded_and_processing_dimensions() -> None:
    result = normalize_to_portrait_3_4(Image.new("RGB", (1600, 900), "white"))

    assert result.as_audit(uploaded_size=(4032, 3024)) == {
        "version": 1,
        "mode": "center_crop",
        "target_ratio": "3:4",
        "uploaded_size": [4032, 3024],
        "input_size": [1600, 900],
        "crop_box": [462, 0, 1137, 900],
        "processed_size": [675, 900],
        "retained_area_ratio": 0.4219,
        "applied": True,
    }


def test_normalization_rejects_image_too_small_for_exact_ratio() -> None:
    with pytest.raises(ValueError, match="尺寸过小"):
        normalize_to_portrait_3_4(Image.new("RGB", (2, 100), "white"))


@pytest.mark.parametrize(
    ("size", "expected"),
    [((3, 4), True), ((1080, 1440), True), ((4, 3), False), ((900, 1199), False)],
)
def test_is_portrait_3_4_requires_an_exact_ratio(
    size: tuple[int, int], expected: bool
) -> None:
    assert is_portrait_3_4(*size) is expected


def test_existing_delivery_resize_preserves_portrait_ratio() -> None:
    normalized = normalize_to_portrait_3_4(Image.new("RGB", (1600, 900), "white"))
    profile = BeautifyProfile(
        id="delivery",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        auto_straighten=False,
        min_output_long_side=2048,
        jpeg_quality=95,
    )

    delivery_bytes = NaturalBeautifyService().prepare_delivery_image(
        encode_jpeg(normalized.image), profile
    )

    with Image.open(BytesIO(delivery_bytes)) as delivery:
        assert delivery.size == (1536, 2048)
        assert is_portrait_3_4(*delivery.size)
