from io import BytesIO
from random import Random

from PIL import Image, ImageDraw, ImageFilter

from src.core.config import Settings
from src.services.images.quality import QualityEngine


def encode(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


def checkerboard(size: tuple[int, int] = (640, 640), cell_size: int = 32) -> Image.Image:
    image = Image.new("L", size, 32)
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell_size):
        for x in range(0, size[0], cell_size):
            if (x // cell_size + y // cell_size) % 2:
                draw.rectangle((x, y, x + cell_size - 1, y + cell_size - 1), fill=224)
    return image


def gradient(size: tuple[int, int] = (640, 640)) -> Image.Image:
    image = Image.new("L", size)
    image.putdata([int(x / (size[0] - 1) * 255) for _ in range(size[1]) for x in range(size[0])])
    return image


def test_quality_engine_returns_normalized_scores_and_raw_values() -> None:
    metrics = QualityEngine(Settings()).evaluate(encode(checkerboard()))

    assert all(
        0 <= score <= 100
        for score in (
            metrics.sharpness_score,
            metrics.exposure_score,
            metrics.contrast_score,
            metrics.noise_score,
        )
    )
    assert {"brightness_mean", "edge_variance", "noise_residual"} <= metrics.raw_metrics.keys()


def test_sharp_image_scores_higher_than_heavily_blurred_image() -> None:
    engine = QualityEngine(Settings())
    sharp = engine.evaluate(encode(checkerboard()))
    blurred = engine.evaluate(encode(checkerboard().filter(ImageFilter.GaussianBlur(radius=30))))

    assert sharp.sharpness_score > blurred.sharpness_score


def test_well_exposed_image_scores_higher_than_underexposed_image() -> None:
    engine = QualityEngine(Settings())
    well_exposed = engine.evaluate(encode(gradient()))
    underexposed = engine.evaluate(encode(Image.new("L", (640, 640), 10)))

    assert well_exposed.exposure_score > underexposed.exposure_score


def test_high_contrast_image_scores_higher_than_low_contrast_image() -> None:
    engine = QualityEngine(Settings())
    high_contrast = engine.evaluate(encode(checkerboard()))
    low_contrast = engine.evaluate(encode(Image.new("L", (640, 640), 128)))

    assert high_contrast.contrast_score > low_contrast.contrast_score


def test_noisy_image_has_lower_noise_score_than_smooth_image() -> None:
    random = Random(42)
    noise = Image.frombytes("L", (640, 640), bytes(random.randrange(256) for _ in range(640 * 640)))
    engine = QualityEngine(Settings())
    smooth = engine.evaluate(encode(gradient()))
    noisy = engine.evaluate(encode(noise))

    assert noisy.noise_score < smooth.noise_score


def test_weighted_quality_score_uses_configured_geometric_formula() -> None:
    score = QualityEngine.calculate_weighted_quality_score(
        sharpness=80,
        exposure=100,
        contrast=100,
        noise=100,
    )

    assert score == 91.87


def test_weighted_quality_score_keeps_equal_scores_unchanged() -> None:
    assert QualityEngine.calculate_weighted_quality_score(
        sharpness=95,
        exposure=95,
        contrast=95,
        noise=95,
    ) == 95
