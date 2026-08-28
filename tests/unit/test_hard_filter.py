from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from src.core.config import Settings
from src.services.images.hard_filter import HardFilterService, RejectCode


def encode(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def checkerboard(size: tuple[int, int] = (640, 640), cell_size: int = 32) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell_size):
        for x in range(0, size[0], cell_size):
            if (x // cell_size + y // cell_size) % 2:
                draw.rectangle((x, y, x + cell_size - 1, y + cell_size - 1), fill="black")
    return image


@pytest.fixture
def service() -> HardFilterService:
    return HardFilterService(Settings())


def test_hard_filter_accepts_normal_textured_image(service: HardFilterService) -> None:
    result = service.evaluate(encode(checkerboard()), width=640, height=640)

    assert result.passed


def test_technical_guard_accepts_small_image_for_ai_decision(service: HardFilterService) -> None:
    result = service.evaluate(encode(Image.new("RGB", (200, 400), "gray")), width=200, height=400)

    assert result.passed


def test_technical_guard_leaves_blank_frame_to_ai(service: HardFilterService) -> None:
    result = service.evaluate(encode(Image.new("RGB", (640, 640), "white")), width=640, height=640)

    assert result.passed
    assert result.warning_codes == ()


def test_technical_guard_rejects_oversized_image() -> None:
    service = HardFilterService(Settings(hard_filter_max_width=1000, hard_filter_max_height=1000))
    result = service.evaluate(b"already-decoded", width=1001, height=800)

    assert result.reject_codes == (RejectCode.IMAGE_TOO_LARGE,)


def test_hard_filter_accepts_portrait_image_with_a_720_pixel_short_side(
    service: HardFilterService,
) -> None:
    result = service.evaluate(encode(checkerboard((720, 1280))), width=720, height=1280)

    assert result.passed
