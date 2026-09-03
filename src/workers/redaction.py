from __future__ import annotations

import asyncio
import logging
from time import perf_counter

import numpy as np

from src.core.config import get_settings
from src.core.metrics import emit_metric
from src.db.session import AsyncSessionLocal
from src.repositories.jobs import ImageJobRepository
from src.services.ai_model_config import load_ai_model_settings
from src.services.images.beautify import NaturalBeautifyService
from src.services.images.beautify_planning import neutralize_beautify_profile
from src.services.images.enhancement_pipeline import (
    decode_pipeline_state,
    encode_mask,
    encode_pipeline_state,
    new_pipeline_state,
    pipeline_object_key,
    pipeline_state_object_key,
)
from src.services.images.processing_vision import selected_standard_from_processing_json
from src.services.images.redaction import decode_image, encode_jpeg
from src.services.images.watermark import WatermarkPreparation, load_watermark_processor
from src.services.jobs.dispatch import EnhancementTaskPublisher, InpaintTaskPublisher
from src.services.managed_profiles import beautify_from_snapshot, redaction_from_snapshot
from src.services.storage.factory import get_storage_provider
from src.workers.celery_app import celery_app
from src.workers.enhancement_common import EnhancementStageTask

logger = logging.getLogger(__name__)


class RedactionDetectionTask(EnhancementStageTask):
    stage_name = "redaction_detection"
    cursor_stage = "redaction"


@celery_app.task(
    base=RedactionDetectionTask,
    name="image.detect_redaction",
    queue="redaction",
    max_retries=0,
)
def detect_redaction(image_id: str) -> None:
    asyncio.run(_detect_redaction(image_id))


async def _detect_redaction(image_id: str) -> None:
    async with AsyncSessionLocal() as session:
        repository = ImageJobRepository(session)
        item = await repository.claim_enhancement(image_id)
        fresh_start = item is not None
        if item is None:
            item = await repository.continue_enhancement(image_id, "redaction")
        if item is None:
            return
        job = await repository.get_config(item.job_id)
        if job is None or job.cancel_requested_at is not None:
            return

        settings = load_ai_model_settings(get_settings())
        selected_standard_id, _ = selected_standard_from_processing_json(
            item.ai_processing_json
        )
        if item.routed_filter_profile_id and selected_standard_id != item.routed_filter_profile_id:
            await repository.fail_item(item, "命中的条件过滤标准不存在，请重试图片处理")
            return

        beautify_profile = beautify_from_snapshot(
            job.beautify_profile_snapshot, job.beautify_profile_id, settings
        )
        redaction_profile = redaction_from_snapshot(
            job.redaction_profile_snapshot,
            legacy_beautify_snapshot=job.beautify_profile_snapshot,
        )
        storage = get_storage_provider()
        state_key = pipeline_state_object_key(item.job_id, item.id)
        if not fresh_start:
            try:
                existing_state = decode_pipeline_state(await storage.download(state_key))
            except Exception:  # noqa: BLE001
                existing_state = None
            if existing_state is not None and existing_state.get("stage") != "redaction":
                recovered_stage = str(existing_state.get("stage"))
                if recovered_stage not in {"inpaint", "enhance"}:
                    raise ValueError(
                        f"unexpected enhancement state during redaction: {recovered_stage}"
                    )
                advanced = await repository.advance_enhancement_stage(
                    item.id,
                    current_stage="redaction",
                    next_stage=recovered_stage,
                )
                if advanced:
                    if recovered_stage == "inpaint":
                        InpaintTaskPublisher().publish(item.id)
                    else:
                        EnhancementTaskPublisher().publish(item.id)
                return
        original_bytes = await storage.download(item.object_key)
        neutral_profile = neutralize_beautify_profile(beautify_profile)
        orientation_result = NaturalBeautifyService().normalize_orientation(
            original_bytes, neutral_profile
        )
        oriented_image = decode_image(orientation_result.image_bytes)
        preparation = _prepare_watermark(
            oriented_image,
            redaction_profile.watermark,
            settings,
            enabled=job.watermark_processing_enabled,
        )

        normalized_key = pipeline_object_key(item.job_id, item.id, "normalized", "jpg")
        mask_key = pipeline_object_key(item.job_id, item.id, "watermark-mask", "png")
        watermark_key = pipeline_object_key(item.job_id, item.id, "watermark", "jpg")
        await storage.upload(normalized_key, orientation_result.image_bytes, "image/jpeg")

        state = new_pipeline_state()
        state.update(
            {
                "stage": "inpaint" if preparation.requires_inpaint else "enhance",
                "normalized_object_key": normalized_key,
                "watermark_mask_object_key": (
                    mask_key if preparation.requires_inpaint else None
                ),
                "watermark_object_key": watermark_key,
                "watermark_requires_inpaint": preparation.requires_inpaint,
                "watermark_audit": preparation.audit,
                "watermark_reasons": list(preparation.reasons),
            }
        )
        if preparation.requires_inpaint:
            await storage.upload(mask_key, encode_mask(preparation.mask), "image/png")
        else:
            output_bytes = (
                encode_jpeg(preparation.image_bgr, quality=beautify_profile.jpeg_quality)
                if preparation.audit.get("status") == "applied"
                else orientation_result.image_bytes
            )
            await storage.upload(watermark_key, output_bytes, "image/jpeg")
        await storage.upload(
            state_key,
            encode_pipeline_state(state),
            "application/json",
        )

        emit_metric(
            logger,
            "enhancement_stage_total",
            labels={
                "stage": "redaction_detection",
                "outcome": "inpaint" if preparation.requires_inpaint else "fast_path",
                "image_id": item.id,
                "job_id": item.job_id,
            },
        )
        next_stage = "inpaint" if preparation.requires_inpaint else "enhance"
        if not await repository.advance_enhancement_stage(
            item.id,
            current_stage="redaction",
            next_stage=next_stage,
        ):
            return
        if preparation.requires_inpaint:
            InpaintTaskPublisher().publish(item.id)
        else:
            EnhancementTaskPublisher().publish(item.id)


def _prepare_watermark(
    image_bgr, config, settings, *, enabled: bool
) -> WatermarkPreparation:
    if not enabled:
        return WatermarkPreparation(
            image_bgr=image_bgr.copy(),
            mask=np.zeros(image_bgr.shape[:2], dtype=np.uint8),
            audit={"enabled": False, "status": "disabled_by_job"},
            requires_inpaint=False,
        )
    if not config.enabled:
        return WatermarkPreparation(
            image_bgr=image_bgr.copy(),
            mask=np.zeros(image_bgr.shape[:2], dtype=np.uint8),
            audit={"enabled": False, "status": "disabled_by_profile"},
            requires_inpaint=False,
        )

    processor = load_watermark_processor(settings)
    started = perf_counter()
    try:
        preparation = processor.prepare_app_overlay(image_bgr, config)
        audit = dict(preparation.audit)
        audit.update(
            {
                "enabled": True,
                "profile_version": processor.profile_version,
                "detection_duration_ms": round((perf_counter() - started) * 1000),
            }
        )
        return WatermarkPreparation(
            preparation.image_bgr,
            preparation.mask,
            audit,
            False,
            preparation.reasons,
        )
    except Exception as exc:
        logger.warning("Watermark detection failed safely", exc_info=True)
        return WatermarkPreparation(
            image_bgr=image_bgr.copy(),
            mask=np.zeros(image_bgr.shape[:2], dtype=np.uint8),
            audit={
                "enabled": True,
                "status": "failed_safe",
                "profile_version": processor.profile_version,
                "detection_duration_ms": round((perf_counter() - started) * 1000),
                "error": str(exc)[:300],
            },
            requires_inpaint=False,
            reasons=("左下角水印检测失败，已安全保留原画面",),
        )
