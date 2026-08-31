from io import BytesIO

from PIL import Image

from src.services.images.beautify_acceptance import (
    acceptance_passed,
    correct_after_preview,
    evaluate_acceptance,
    make_preview,
)
from src.services.profiles import BeautifyProfile


def _jpeg(color: str = "#777777", size: tuple[int, int] = (1200, 800)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="JPEG", quality=95)
    return output.getvalue()


def _profile() -> BeautifyProfile:
    return BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1.2,
        contrast=1.1,
        color=1.2,
        sharpness=1.5,
        jpeg_quality=90,
    )


def test_preview_has_bounded_long_side_and_neutral_roundtrip_passes() -> None:
    preview = make_preview(_jpeg())
    with Image.open(BytesIO(preview)) as image:
        assert max(image.size) == 768

    assert acceptance_passed(evaluate_acceptance(preview, preview))


def test_preview_correction_converges_all_failed_dimensions_once() -> None:
    checks = [
        {"name": name, "passed": False} for name in ("exposure", "color", "noise", "sharpening")
    ]

    corrected = correct_after_preview(_profile(), checks)

    assert corrected.profile.brightness == 1.03
    assert corrected.profile.color == 1.05
    assert corrected.profile.sharpness == 1.05
    assert corrected.profile.local_clarity_strength == 0.1
    assert len(corrected.reasons) == 4
