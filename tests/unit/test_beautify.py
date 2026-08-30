from io import BytesIO

from PIL import Image, ImageDraw

from src.services.images.beautify import NaturalBeautifyService
from src.services.profiles import BeautifyProfile


def test_natural_beautify_returns_a_2k_jpeg_for_smaller_source_images() -> None:
    source = BytesIO()
    Image.new("RGB", (640, 480), "#606060").save(source, format="PNG")
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1.03,
        contrast=1.08,
        color=1.03,
        sharpness=1.08,
        jpeg_quality=90,
    )

    enhanced = NaturalBeautifyService().enhance(source.getvalue(), profile)

    with Image.open(BytesIO(enhanced)) as image:
        assert image.format == "JPEG"
        assert image.size == (2048, 1536)


def test_natural_beautify_reports_only_non_destructive_processing_steps() -> None:
    source = BytesIO()
    Image.new("RGB", (640, 480), "#606060").save(source, format="JPEG")
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        jpeg_quality=90,
    )
    result = NaturalBeautifyService().enhance_with_details(source.getvalue(), profile)

    assert NaturalBeautifyService.processing_reasons(profile, result) == [
        "已保留原始构图与画面比例",
        "已完成白平衡和色温微调",
        "已平衡高光和阴影细节",
        "已增强暗部和纹理区域的局部层次",
        "已进行轻度降噪",
        "已增强主体与纹理边缘细节",
    ]


def test_natural_beautify_reduces_small_internal_glare() -> None:
    image = Image.new("RGB", (640, 480), "#808080")
    ImageDraw.Draw(image).rectangle((250, 170, 330, 250), fill="white")
    source = BytesIO()
    image.save(source, format="JPEG")
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        auto_white_balance=False,
        shadow_lift=0,
        highlight_recovery=0,
        denoise_strength=0,
        jpeg_quality=90,
    )

    result = NaturalBeautifyService().enhance_with_details(source.getvalue(), profile)

    assert result.glare_reduction_applied is True
    assert result.local_clarity_applied is True
    assert result.local_tone_applied is True


def test_local_tone_enhancement_changes_uneven_lighting_without_changing_size() -> None:
    image = Image.new("RGB", (640, 480), "#777777")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 319, 479), fill="#303030")
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        local_tone_strength=0.25,
        jpeg_quality=90,
    )

    enhanced, applied = NaturalBeautifyService._enhance_local_tone(image, profile)

    assert applied is True
    assert enhanced.size == image.size
    assert enhanced.tobytes() != image.tobytes()


def test_natural_beautify_corrects_a_small_camera_tilt() -> None:
    grid = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(grid)
    for position in range(40, 640, 80):
        draw.line((position, 0, position, 480), fill="black", width=5)
    for position in range(40, 480, 80):
        draw.line((0, position, 640, position), fill="black", width=5)
    source = BytesIO()
    grid.rotate(2.5, resample=Image.Resampling.BICUBIC, fillcolor="white").save(source, format="JPEG")
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        auto_white_balance=False,
        shadow_lift=0,
        highlight_recovery=0,
        denoise_strength=0,
        jpeg_quality=90,
    )

    assert NaturalBeautifyService._estimate_skew_angle(
        grid.rotate(2.5, resample=Image.Resampling.BICUBIC, fillcolor="white"),
        3,
    ) < 0
    result = NaturalBeautifyService().enhance_with_details(source.getvalue(), profile)

    assert result.straighten_applied is True
    with Image.open(BytesIO(result.image_bytes)) as enhanced:
        residual_angle = NaturalBeautifyService._estimate_skew_angle(enhanced.convert("RGB"), 3)
    assert residual_angle is None or abs(residual_angle) < 0.5


def test_natural_beautify_keeps_an_already_straight_photo_unchanged() -> None:
    grid = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(grid)
    for position in range(40, 640, 80):
        draw.line((position, 0, position, 480), fill="black", width=5)
    for position in range(40, 480, 80):
        draw.line((0, position, 640, position), fill="black", width=5)
    source = BytesIO()
    grid.save(source, format="JPEG")
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        auto_white_balance=False,
        shadow_lift=0,
        highlight_recovery=0,
        denoise_strength=0,
        jpeg_quality=90,
    )

    result = NaturalBeautifyService().enhance_with_details(source.getvalue(), profile)

    assert result.straighten_applied is False


def test_orientation_normalization_applies_exif_rotation_and_larger_confirmed_tilt() -> None:
    grid = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(grid)
    for position in range(40, 640, 80):
        draw.line((position, 0, position, 480), fill="black", width=5)
    for position in range(40, 480, 80):
        draw.line((0, position, 640, position), fill="black", width=5)
    source = BytesIO()
    exif = grid.getexif()
    exif[274] = 3
    grid.rotate(8, resample=Image.Resampling.BICUBIC, fillcolor="white").save(
        source,
        format="JPEG",
        exif=exif,
    )
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        auto_white_balance=False,
        shadow_lift=0,
        highlight_recovery=0,
        denoise_strength=0,
        max_straighten_degrees=12,
        jpeg_quality=90,
    )

    result = NaturalBeautifyService().normalize_orientation(source.getvalue(), profile)

    assert result.exif_orientation_applied is True
    assert result.straighten_applied is True


def test_delivery_image_is_upscaled_to_the_minimum_long_side() -> None:
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1,
        jpeg_quality=95,
        min_output_long_side=2048,
    )
    image = Image.new("RGB", (1280, 720), "white")

    output = NaturalBeautifyService._ensure_minimum_output_size(image, profile)

    assert output.size == (2048, 1152)
