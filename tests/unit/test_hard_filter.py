from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFilter

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


def test_hard_filter_early_exits_for_small_image(service: HardFilterService) -> None:
    result = service.evaluate(encode(Image.new("RGB", (200, 400), "gray")), width=200, height=400)

    assert result.reject_codes == (RejectCode.IMAGE_TOO_SMALL,)


def test_hard_filter_rejects_a_blank_overexposed_frame(service: HardFilterService) -> None:
    result = service.evaluate(encode(Image.new("RGB", (640, 640), "white")), width=640, height=640)

    assert not result.passed
    assert RejectCode.EXTREME_OVEREXPOSURE in result.warning_codes
    assert RejectCode.SOLID_COLOR in result.reject_codes


def test_hard_filter_rejects_a_blank_solid_color_frame(service: HardFilterService) -> None:
    result = service.evaluate(encode(Image.new("RGB", (640, 640), "#7f7f7f")), width=640, height=640)

    assert not result.passed
    assert result.reject_codes == (RejectCode.SOLID_COLOR,)


def test_hard_filter_rejects_a_near_black_frame_with_only_a_tiny_bright_area(
    service: HardFilterService,
) -> None:
    image = Image.new("RGB", (640, 640), "black")
    ImageDraw.Draw(image).rectangle((300, 300, 339, 339), fill="white")

    result = service.evaluate(encode(image), width=640, height=640)

    assert not result.passed
    assert RejectCode.EXTREME_UNDEREXPOSURE in result.reject_codes


def test_hard_filter_retains_a_dark_scene_when_material_detail_is_visible(
    service: HardFilterService,
) -> None:
    image = Image.new("RGB", (640, 640), "black")
    ImageDraw.Draw(image).rectangle((200, 120, 439, 519), fill="#606060")

    result = service.evaluate(encode(image), width=640, height=640)

    assert result.passed


def test_hard_filter_rejects_extreme_blur_before_beautification(service: HardFilterService) -> None:
    image = Image.new("RGB", (640, 640), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 319, 639), fill="#2d2d2d")
    draw.rectangle((330, 100, 610, 540), fill="#828282")
    image = image.filter(ImageFilter.GaussianBlur(radius=20))
    result = service.evaluate(encode(image), width=640, height=640)

    assert not result.passed
    assert result.reject_codes == (RejectCode.EXTREME_BLUR,)


def test_hard_filter_accepts_an_otherwise_valid_image_with_a_dark_region(
    service: HardFilterService,
) -> None:
    image = Image.new("RGB", (640, 640), "white")
    ImageDraw.Draw(image).rectangle((480, 480, 639, 639), fill="#202020")

    result = service.evaluate(encode(image), width=640, height=640)

    assert RejectCode.LOCAL_HEAVY_SHADOW not in result.reject_codes


def test_hard_filter_accepts_portrait_image_with_a_720_pixel_short_side(
    service: HardFilterService,
) -> None:
    result = service.evaluate(encode(checkerboard((720, 1280))), width=720, height=1280)

    assert result.passed
