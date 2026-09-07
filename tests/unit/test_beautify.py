from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageDraw

from src.services.images.beautify import NaturalBeautifyService
from src.services.profiles import BeautifyProfile


def test_memory_bounded_blending_preserves_original_vectorized_pixels() -> None:
    """Use the original formulas as an oracle for the buffer reuse optimization."""
    rgb = np.random.default_rng(42).integers(0, 256, (151, 201, 3), dtype=np.uint8)
    image = Image.fromarray(rgb)
    profile = BeautifyProfile(
        id="compatibility", version=1, description="test", brightness=1,
        contrast=1, color=1, sharpness=1.08, jpeg_quality=90,
        local_clarity_strength=0.14,
    )
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gradient = cv2.magnitude(
        cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3),
        cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3),
    )
    edge = cv2.GaussianBlur(np.clip((gradient - 28) / 90, 0, 1), (0, 0), sigmaX=1.2)
    detailed = cv2.addWeighted(rgb, 1.35, cv2.GaussianBlur(rgb, (0, 0), sigmaX=1.1), -0.35, 0)
    alpha = (edge * profile.local_clarity_strength)[:, :, None]
    expected = rgb.astype(np.float32) * (1 - alpha) + detailed.astype(np.float32) * alpha
    actual, applied = NaturalBeautifyService._enhance_local_clarity(image, profile)
    assert applied
    np.testing.assert_array_equal(np.asarray(actual), np.clip(expected, 0, 255).astype(np.uint8))

    floats = rgb.astype(np.float32)
    detail = floats - cv2.GaussianBlur(floats, (0, 0), sigmaX=1.0)
    edge_strength = np.max(np.abs(detail), axis=2)
    mask = cv2.GaussianBlur((edge_strength >= 6).astype(np.float32), (0, 0), sigmaX=0.8)
    expected = floats + detail * min(1.5, (profile.sharpness - 1) * 1.5) * mask[:, :, None]
    actual, applied = NaturalBeautifyService._apply_output_sharpness(image, profile)
    assert applied
    np.testing.assert_array_equal(np.asarray(actual), np.clip(expected, 0, 255).astype(np.uint8))


def test_lightness_only_glare_blending_preserves_color_channels_and_pixels() -> None:
    rgb = np.full((151, 201, 3), 128, dtype=np.uint8)
    rgb[60:76, 90:106] = 255
    profile = BeautifyProfile(
        id="compatibility", version=1, description="test", brightness=1,
        contrast=1, color=1, sharpness=1, jpeg_quality=90, glare_reduction_strength=0.2,
    )
    mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
    mask[60:76, 90:106] = 255
    alpha = cv2.GaussianBlur(mask, (0, 0), sigmaX=5).astype(np.float32) / 255
    alpha *= profile.glare_reduction_strength
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    compressed = 215 + (lab[:, :, 0] - 215) * 0.45
    lab[:, :, 0] = lab[:, :, 0] * (1 - alpha) + compressed * alpha
    expected = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
    actual, applied = NaturalBeautifyService._reduce_glare(Image.fromarray(rgb), profile)
    assert applied
    np.testing.assert_array_equal(np.asarray(actual), expected)


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


def test_output_sharpness_changes_edges_without_changing_dimensions() -> None:
    image = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 60, 240, 180), fill="gray")
    profile = BeautifyProfile(
        id="test",
        version=1,
        description="test",
        brightness=1,
        contrast=1,
        color=1,
        sharpness=1.5,
        jpeg_quality=95,
    )

    sharpened, applied = NaturalBeautifyService._apply_output_sharpness(image, profile)

    assert applied is True
    assert sharpened.size == image.size
    assert sharpened.tobytes() != image.tobytes()
