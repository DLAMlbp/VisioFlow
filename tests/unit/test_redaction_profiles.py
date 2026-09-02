import pytest
from pydantic import ValidationError

from src.services.profiles import BeautifyProfile, LogoMosaicConfig, WatermarkRemovalConfig


def test_beautify_redaction_defaults_are_disabled() -> None:
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

    assert profile.watermark_removal.enabled is False
    assert profile.logo_mosaic.enabled is False


def test_watermark_roi_rejects_unsafe_or_invalid_regions() -> None:
    with pytest.raises(ValidationError):
        WatermarkRemovalConfig(roi=(0.5, 0.8, 0.4, 1))
    with pytest.raises(ValidationError):
        WatermarkRemovalConfig(roi=(0, 0, 1, 1), preserve_outside_roi=True)


def test_redaction_configuration_accepts_supported_brand_only() -> None:
    config = LogoMosaicConfig(enabled=True, targets=["dangjia_logo"])

    assert config.targets == ["dangjia_logo"]
